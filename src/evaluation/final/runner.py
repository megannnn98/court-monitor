"""Runs every final-evaluation case through the real product workflow.

Per case, on a disposable database that is truncated first:

    Rosfinmonitoring snapshot + pre-existing persons (fixtures)
    → fixture source → MonitoringService.run_source (ingest, extract, ER v2,
      classification, RF matching, findings) — once per planned run
    → LangGraph research workflow with a fixture request parser (the LLM is
      replaced; everything after intake is the production code)
    → ResearchReport → provenance verification

No Internet, no Together AI, no embedding model in the deterministic baseline.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from application import build_monitoring_service
from candidates.models import RosfinmonitoringStatus
from candidates.service import CandidateQueryService
from db.database import create_session_factory
from db.maintenance import require_disposable_database, truncate_disposable_tables
from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    MonitoringFindingRecord,
    ParsedArticleRecord,
    PersecutionClassificationRecord,
    PersonResolutionDecisionRecord,
    RosfinMatchRecord,
    SourceDocument,
)
from evaluation.final.corpus import (
    RF_NO_MATCH_RECORD,
    RF_NO_SNAPSHOT,
    FinalCase,
    FinalCorpus,
    FinalIdentity,
    FinalResearchCheck,
)
from evaluation.final.models import CaseResult, Counts, FalsePositive, ResearchOutcome
from evaluation.final.sources import FixtureUpstream, UnusedFetcher, fixture_source
from extraction.normalizers import RuleBasedMentionNormalizer
from monitoring.models import MonitoringSettings
from persecution.queries import latest_persecution_classification_ids
from persons.persistence import SqlAlchemyPersonPersistence
from research.models import MAX_RESEARCH_LIMIT, ResearchRequest
from research.planning.planner import ResearchPlanner
from research.reports.provenance import verify_report_provenance
from research.repository import SqlAlchemyPersonResearchRepository
from research.service import ResearchService
from research.workflow.graph import build_research_graph, run_research_query
from research.workflow.intake import PreparedRequestParser
from research.workflow.models import WorkflowStatus
from rosfinmonitoring.ingestion import RosfinmonitoringIngestionPipeline
from rosfinmonitoring.persistence import RosfinmonitoringPersistence
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup
from sources.source_registry import SOURCES

logger = logging.getLogger("evaluation")

PENDING_REVIEW = "pending_review"
POLITICAL = "political"


@dataclass
class _MentionRow:
    article: str
    surface: str
    mention_id: int
    person_id: int | None


@dataclass
class _CaseState:
    mentions: list[_MentionRow] = field(default_factory=list)
    decisions: dict[int, tuple[str, str]] = field(default_factory=dict)
    seed_person_ids: list[int] = field(default_factory=list)


class FinalEvaluationRunner:
    def __init__(self, engine: Engine) -> None:
        require_disposable_database(engine)
        self._engine = engine
        self._session_factory = create_session_factory(engine)

    def run(self, corpus: FinalCorpus) -> list[CaseResult]:
        results = []
        for case in corpus.cases:
            logger.info("event=final_evaluation_case_started case=%s", case.id)
            results.append(self.run_case(case))
        truncate_disposable_tables(self._engine)
        return results

    def run_case(self, case: FinalCase) -> CaseResult:
        if case.requires_semantic_models:
            return CaseResult(
                case_id=case.id,
                categories=case.categories,
                skipped_reason="requires real embedding models (not part of the baseline)",
                known_limitation=case.known_limitation,
            )
        truncate_disposable_tables(self._engine)
        session_factory = self._session_factory
        result = CaseResult(
            case_id=case.id, categories=case.categories, known_limitation=case.known_limitation
        )
        snapshot_id = self._import_snapshot(case)
        state = _CaseState(seed_person_ids=self._seed_persons(case))

        upstream = FixtureUpstream()
        service = build_monitoring_service(
            session_factory,
            settings=MonitoringSettings(enabled_sources=("ovd-info",), discovery_limit=100),
            env={},
            sources={"ovd-info": fixture_source(upstream)},
            create_fetcher=UnusedFetcher,
            use_env_semantic_indexer=False,
            # Runs follow each other within seconds; the settle interval would
            # only add idempotent recomputation.
            evidence_settle_interval=timedelta(0),
        )
        articles = {article.external_id: article for article in case.articles}
        for index, run_articles in enumerate(case.run_plan):
            for external_id in run_articles:
                article = articles[external_id]
                upstream.publish(article.external_id, article.title, article.text)
            run = service.run_source("ovd-info")
            if index < len(case.run_expectations):
                expected = case.run_expectations[index]
                for name in ("documents_ingested", "documents_skipped", "findings_created"):
                    expected_value = getattr(expected, name)
                    if expected_value is not None:
                        result.check(
                            f"run{index + 1}.{name}",
                            getattr(run, name) == expected_value,
                            f"expected {expected_value}, got {getattr(run, name)}",
                        )
            result.check(f"run{index + 1}.status", run.status.value != "failed", run.status.value)

        with session_factory() as session:
            self._load_state(session, state)
            self._score_extraction(case, state, result)
            person_by_identity = self._score_identities(case, state, result)
            self._score_derived(session, case, snapshot_id, person_by_identity, result)
        self._score_research(case, person_by_identity, snapshot_id, result)

        pending = sum(1 for _, status in state.decisions.values() if status == PENDING_REVIEW)
        result.review_required = (
            pending > 0
            or any(research.review_required for research in result.research)
            or self._any_person_requires_review(snapshot_id)
        )
        if case.review_required is not None:
            result.check(
                "review_required",
                result.review_required == case.review_required,
                f"expected {case.review_required}, got {result.review_required}",
            )
        return result

    def _any_person_requires_review(self, snapshot_id: int | None) -> bool:
        """The domain review policy (research warnings) over every person of the case."""
        criteria: dict[str, object] = {} if snapshot_id is None else {"snapshot_id": snapshot_id}
        response = ResearchService(
            repository=SqlAlchemyPersonResearchRepository(self._session_factory),
            candidate_query=CandidateQueryService(self._session_factory),
        ).execute(
            ResearchRequest.model_validate(
                {"object_type": "person", "criteria": criteria, "limit": MAX_RESEARCH_LIMIT}
            )
        )
        return any(result.review_required for result in response.results)

    # -- fixtures ---------------------------------------------------------------------------

    def _import_snapshot(self, case: FinalCase) -> int | None:
        if case.rf_snapshot is None:
            return None
        lines = ["full_name,birth_date,inclusion_reason"]
        lines += [f"{name},{birth_date},evaluation" for name, birth_date in case.rf_snapshot]
        from datetime import UTC, datetime

        return (
            RosfinmonitoringIngestionPipeline(
                persistence=RosfinmonitoringPersistence(self._session_factory)
            )
            .ingest(
                raw_content=("\n".join(lines) + "\n").encode(),
                source_url=f"https://rosfinmonitoring.test/{case.id}",
                snapshot_date=datetime(2026, 1, 1, tzinfo=UTC),
            )
            .snapshot_id
        )

    def _seed_persons(self, case: FinalCase) -> list[int]:
        persistence = SqlAlchemyPersonPersistence(self._session_factory)
        return [
            persistence.create_person(
                canonical_name=name,
                normalized_name=name,
                matching_key=RuleBasedMentionNormalizer._matching_key(name),
            )
            for name in case.seed_persons
        ]

    # -- state ------------------------------------------------------------------------------

    @staticmethod
    def _load_state(session: Session, state: _CaseState) -> None:
        rows = session.execute(
            select(
                SourceDocument.external_id,
                EntityMentionRecord.surface_text,
                EntityMentionRecord.id,
                EntityMentionRecord.person_id,
            )
            .join(
                ArticleExtractionRunRecord,
                ArticleExtractionRunRecord.id == EntityMentionRecord.extraction_run_id,
            )
            .join(
                ParsedArticleRecord, ParsedArticleRecord.id == ArticleExtractionRunRecord.article_id
            )
            .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
            .where(EntityMentionRecord.entity_type == "person")
            .order_by(EntityMentionRecord.id)
        ).all()
        state.mentions = [
            _MentionRow(article=article, surface=surface, mention_id=mention_id, person_id=person)
            for article, surface, mention_id, person in rows
        ]
        state.decisions = {
            mention_id: (action, status)
            for mention_id, action, status in session.execute(
                select(
                    PersonResolutionDecisionRecord.mention_id,
                    PersonResolutionDecisionRecord.action,
                    PersonResolutionDecisionRecord.status,
                )
            ).all()
        }

    # -- scoring ----------------------------------------------------------------------------

    @staticmethod
    def _score_extraction(case: FinalCase, state: _CaseState, result: CaseResult) -> None:
        counts = Counts()
        for article, expected_surfaces in case.expected_extraction.items():
            actual = Counter(m.surface for m in state.mentions if m.article == article)
            expected = Counter(expected_surfaces)
            tp = sum((actual & expected).values())
            counts.tp += tp
            counts.fp += sum(actual.values()) - tp
            counts.fn += sum(expected.values()) - tp
            missing = sorted((expected - actual).elements())
            extra = sorted((actual - expected).elements())
            result.check(
                f"extraction[{article}]",
                not missing and not extra,
                f"missing {missing}, unexpected {extra}",
            )
        result.extraction = counts

    @staticmethod
    def _identity_mentions(identity: FinalIdentity, state: _CaseState) -> list[_MentionRow]:
        wanted = {(ref.article, ref.surface) for ref in identity.mentions}
        return [m for m in state.mentions if (m.article, m.surface) in wanted]

    def _score_identities(
        self, case: FinalCase, state: _CaseState, result: CaseResult
    ) -> dict[str, int | None]:
        holders: dict[int, set[str]] = {}
        person_by_identity: dict[str, int | None] = {}
        for key, identity in case.identities.items():
            mentions = self._identity_mentions(identity, state)
            persons = {m.person_id for m in mentions if m.person_id is not None}
            if identity.seed_index is not None:
                persons.add(state.seed_person_ids[identity.seed_index])
            for person_id in persons:
                holders.setdefault(person_id, set()).add(key)
            person_by_identity[key] = next(iter(persons)) if len(persons) == 1 else None
            if identity.mentions:
                result.check(
                    f"identity[{key}].extracted",
                    len({(m.article, m.surface) for m in mentions})
                    == len({(r.article, r.surface) for r in identity.mentions}),
                    "some mentions were not extracted",
                )
            if identity.er_review is not None:
                reviewed = any(
                    state.decisions.get(m.mention_id, ("", ""))[1] == PENDING_REVIEW
                    for m in mentions
                )
                result.check(
                    f"identity[{key}].er_review",
                    reviewed == identity.er_review,
                    f"expected review={identity.er_review}, got {reviewed}",
                )
                if identity.er_review:
                    continue
            if identity.mentions and not identity.er_review:
                unresolved = sum(1 for m in mentions if m.person_id is None)
                result.check(
                    f"identity[{key}].single_person",
                    len(persons) == 1 and unresolved == 0,
                    f"resolved to {len(persons)} persons, {unresolved} mentions unlinked",
                )

        shared = {person: keys for person, keys in holders.items() if len(keys) > 1}
        for person_id, keys in sorted(shared.items()):
            result.false_positives.append(
                FalsePositive(
                    kind="false_person_link",
                    case_id=case.id,
                    identity=",".join(sorted(keys)),
                    detail=f"person {person_id} holds {len(keys)} different real people",
                    gated=case.known_limitation is None,
                )
            )
            result.check(f"person[{person_id}].no_false_link", False, f"shared by {sorted(keys)}")
            for key in keys:
                person_by_identity[key] = None

        for mention in state.mentions:
            action = state.decisions.get(mention.mention_id, ("", ""))[0]
            if action != "auto_link" or mention.person_id is None:
                continue
            result.er.auto_links += 1
            if mention.person_id not in shared:
                result.er.auto_links_correct += 1
        result.er.reviews = sum(
            1 for _, status in state.decisions.values() if status == PENDING_REVIEW
        )
        result.er.false_links = sum(len(keys) - 1 for keys in shared.values())
        return person_by_identity

    def _score_derived(
        self,
        session: Session,
        case: FinalCase,
        snapshot_id: int | None,
        person_by_identity: dict[str, int | None],
        result: CaseResult,
    ) -> None:
        person_ids = [person for person in person_by_identity.values() if person is not None]
        classifications = dict(
            session.execute(
                select(
                    PersecutionClassificationRecord.person_id,
                    PersecutionClassificationRecord.status,
                ).where(
                    PersecutionClassificationRecord.id.in_(latest_persecution_classification_ids()),
                    PersecutionClassificationRecord.person_id.in_(person_ids),
                )
            )
            .tuples()
            .all()
        )
        matches: dict[int, str] = {}
        candidates: set[int] = set()
        if snapshot_id is not None:
            matches = dict(
                session.execute(
                    select(RosfinMatchRecord.person_id, RosfinMatchRecord.status).where(
                        RosfinMatchRecord.snapshot_id == snapshot_id,
                        RosfinMatchRecord.person_id.in_(person_ids),
                    )
                )
                .tuples()
                .all()
            )
            candidates = {
                candidate.person_id
                for candidate in CandidateQueryService(session)
                .get_candidates(snapshot_id, limit=None)
                .candidates
            }
        findings = set(
            session.scalars(
                select(MonitoringFindingRecord.person_id).where(
                    MonitoringFindingRecord.active.is_(True)
                )
            ).all()
        )

        for key, identity in case.identities.items():
            person_id = person_by_identity.get(key)
            if identity.persecution is not None:
                actual = None if person_id is None else classifications.get(person_id)
                correct = actual in identity.persecution
                result.persecution.append((identity.persecution, actual))
                result.check(
                    f"identity[{key}].persecution",
                    correct,
                    f"expected {identity.persecution}, got {actual}",
                )
                if actual == POLITICAL and POLITICAL not in identity.persecution:
                    result.false_positives.append(
                        FalsePositive(
                            kind="false_political_classification",
                            case_id=case.id,
                            identity=key,
                            detail=f"expected {identity.persecution}",
                            gated=case.known_limitation is None,
                        )
                    )
            if identity.rf_status is not None:
                if snapshot_id is None:
                    actual_rf: str | None = RF_NO_SNAPSHOT
                elif person_id is None:
                    actual_rf = None
                else:
                    actual_rf = matches.get(person_id, RF_NO_MATCH_RECORD)
                result.rf.append((identity.rf_status, actual_rf))
                result.check(
                    f"identity[{key}].rf_status",
                    actual_rf == identity.rf_status,
                    f"expected {identity.rf_status}, got {actual_rf}",
                )
                if (
                    actual_rf == RosfinmonitoringStatus.NOT_MATCHED.value
                    and identity.rf_status != RosfinmonitoringStatus.NOT_MATCHED.value
                ):
                    result.false_positives.append(
                        FalsePositive(
                            kind="false_rf_not_matched",
                            case_id=case.id,
                            identity=key,
                            detail=f"expected {identity.rf_status}",
                            gated=case.known_limitation is None,
                        )
                    )
            if identity.candidate is not None:
                is_candidate = person_id is not None and person_id in candidates
                is_finding = person_id is not None and person_id in findings
                result.candidates.add(expected=identity.candidate, actual=is_candidate)
                result.actionable.add(expected=identity.candidate, actual=is_finding)
                result.check(
                    f"identity[{key}].candidate",
                    is_candidate == identity.candidate,
                    f"expected {identity.candidate}, got {is_candidate}",
                )
                result.check(
                    f"identity[{key}].finding",
                    is_finding == identity.candidate,
                    f"expected {identity.candidate}, got {is_finding}",
                )
                if is_finding and not identity.candidate:
                    result.false_positives.append(
                        FalsePositive(
                            kind="false_actionable_candidate",
                            case_id=case.id,
                            identity=key,
                            detail="monitoring finding for a person who must not be one",
                            gated=case.known_limitation is None,
                        )
                    )

    def _score_research(
        self,
        case: FinalCase,
        person_by_identity: dict[str, int | None],
        snapshot_id: int | None,
        result: CaseResult,
    ) -> None:
        identity_by_person = {
            person: key for key, person in person_by_identity.items() if person is not None
        }
        session_factory = self._session_factory
        for index, check in enumerate(case.research):
            outcome = self._run_research(session_factory, check)
            returned = outcome.person_ids
            expected = set(check.expected_identities)
            actual_keys = {
                identity_by_person.get(person, f"person:{person}") for person in returned
            }
            outcome.counts.tp = len(expected & actual_keys)
            outcome.counts.fp = len(actual_keys - expected)
            outcome.counts.fn = len(expected - actual_keys)
            result.check(
                f"research[{index}].status",
                outcome.status == WorkflowStatus.COMPLETED.value,
                f"workflow {outcome.status}: {outcome.error}",
            )
            result.check(
                f"research[{index}].persons",
                actual_keys == expected,
                f"expected {sorted(expected)}, got {sorted(actual_keys)}",
            )
            result.check(
                f"research[{index}].provenance",
                not outcome.provenance_problems,
                "; ".join(outcome.provenance_problems),
            )
            missing_evidence = sorted(
                set(check.expected_evidence_articles) - outcome.cited_articles
            )
            result.check(
                f"research[{index}].evidence",
                not missing_evidence,
                f"report does not cite {missing_evidence}",
            )
            for problem in outcome.provenance_problems:
                result.false_positives.append(
                    FalsePositive(
                        kind="unsupported_report_claim",
                        case_id=case.id,
                        identity=None,
                        detail=problem,
                        gated=case.known_limitation is None,
                    )
                )
            result.research.append(outcome)

    @staticmethod
    def _run_research(
        session_factory: sessionmaker[Session], check: FinalResearchCheck
    ) -> ResearchOutcome:
        graph = build_research_graph(
            request_parser=PreparedRequestParser(check.request),
            research_service=ResearchService(
                repository=SqlAlchemyPersonResearchRepository(session_factory),
                candidate_query=CandidateQueryService(session_factory),
            ),
            snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory),
            planner=ResearchPlanner(SOURCES),
        )
        query_result = run_research_query(graph, check.query)
        outcome = ResearchOutcome(
            query=check.query,
            status=query_result.status.value,
            error=None if query_result.error is None else query_result.error.code.value,
            person_ids=[r.person.id for r in query_result.results if r.person.id is not None],
        )
        report = query_result.report
        if report is None:
            return outcome
        outcome.report_status = report.status.value
        outcome.review_required = report.review_required
        outcome.claims = sum(len(item.claims) for item in report.items)
        with session_factory() as session:
            problems = verify_report_provenance(session, report, query_result.results)
            cited_article_ids = {
                citation.article_id
                for item in report.items
                for claim in item.claims
                for citation in claim.citations
            }
            outcome.cited_articles = set(
                session.scalars(
                    select(SourceDocument.external_id)
                    .join(ParsedArticleRecord, ParsedArticleRecord.document_id == SourceDocument.id)
                    .where(ParsedArticleRecord.id.in_(cited_article_ids))
                ).all()
            )
        outcome.provenance_problems = [
            f"person {p.person_id} {p.claim_type or 'item'}: {p.problem}" for p in problems
        ]
        return outcome


def run_final_evaluation(engine: Engine, corpus: FinalCorpus) -> Sequence[CaseResult]:
    return FinalEvaluationRunner(engine).run(corpus)

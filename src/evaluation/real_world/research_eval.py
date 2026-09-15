"""Research query benchmark: request intake, execution, report claims.

Intake with the real language model runs only when Together AI is configured;
otherwise it is NOT_RUN. Execution always runs the production LangGraph
workflow with the expected structured request (the deterministic part).

Every report claim about a golden person is classified SUPPORTED,
PARTIALLY_SUPPORTED, UNSUPPORTED or CONTRADICTED against the annotations;
claims about persons or articles outside the golden dataset are NOT_EVALUATED.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy.orm import Session, sessionmaker

from candidates.service import CandidateQueryService
from evaluation.final.models import Counts
from evaluation.real_world.component_evaluation import IdentityMap
from evaluation.real_world.db_state import PipelineState
from evaluation.real_world.golden import (
    DangerousKind,
    GoldenDataset,
    GoldenPerson,
    GoldenReportExpectation,
    GoldenSplit,
    RosfinExpectedStatus,
)
from evaluation.real_world.metrics import rate
from evaluation.real_world.models import DEFAULT_DATA_DIR
from evaluation.real_world.results import (
    ClaimSupport,
    ErrorComponent,
    Failure,
    ResearchSection,
    SectionStatus,
    Severity,
)
from research.planning.planner import ResearchPlanner
from research.reports.models import ResearchClaim, ResearchClaimType, ResearchReportItem
from research.repository import SqlAlchemyPersonResearchRepository
from research.service import ResearchService
from research.workflow.graph import build_research_graph, run_research_query
from research.workflow.intake import PreparedRequestParser, ResearchRequestParser
from research.workflow.models import ResearchQueryResult, WorkflowStatus
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup
from sources.source_registry import SOURCES

DEFAULT_RESEARCH_QUERIES_PATH = DEFAULT_DATA_DIR / "research_queries.json"
SNAPSHOT_PLACEHOLDER = "$SNAPSHOT"
ABSENCE_WORDING = "Не найден в перечне"

QueryClass = Literal[
    "main_candidate",
    "person_lookup",
    "event",
    "date_range",
    "source_restriction",
    "persecution_status",
    "rf_status",
    "semantic_wording",
    "ambiguous_person",
    "unsupported_criteria",
]


class ResearchQueryCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: str
    query_class: QueryClass
    text: str
    split: GoldenSplit
    # What a correct intake produces; "$SNAPSHOT" stands for the evaluation snapshot id.
    expected_request: dict[str, Any] | None = None
    # The query cannot be executed without asking the user.
    expect_clarification: bool = False
    # Criterion names the query contains that ResearchRequest cannot express.
    unsupported_criteria: list[str] = Field(default_factory=list)
    # Golden persons that must be returned.
    expected_persons: list[str] = Field(default_factory=list)
    # When true, any other golden person in the result is a false positive.
    expected_persons_exhaustive: bool = False
    report: GoldenReportExpectation | None = None
    requires_semantic: bool = False
    notes: str | None = None


def load_research_queries(path: Path = DEFAULT_RESEARCH_QUERIES_PATH) -> list[ResearchQueryCase]:
    if not path.exists():
        return []
    return TypeAdapter(list[ResearchQueryCase]).validate_json(path.read_bytes())


def _substitute(request: dict[str, Any], snapshot_id: int | None) -> dict[str, Any]:
    text = json.dumps(request)
    if snapshot_id is None:
        payload: dict[str, Any] = json.loads(text)
        criteria = payload.get("criteria", {})
        if criteria.get("snapshot_id") == SNAPSHOT_PLACEHOLDER:
            criteria.pop("snapshot_id")
            criteria.pop("rosfinmonitoring_status", None)
        return payload
    return dict(json.loads(text.replace(f'"{SNAPSHOT_PLACEHOLDER}"', str(snapshot_id))))


def _golden_of(identity: IdentityMap, person_id: int) -> str | None:
    golden = identity.golden_of_person.get(person_id, set())
    if len(golden) != 1 or person_id in identity.false_link_persons:
        return None
    return next(iter(golden))


class ClaimJudge:
    """Classify report claims against the golden dataset."""

    def __init__(self, dataset: GoldenDataset, state: PipelineState, identity: IdentityMap) -> None:
        self._dataset = dataset
        self._state = state
        self._identity = identity
        self._persons = {p.golden_person_id: p for p in dataset.persons}
        self._articles_by_key = {a.key: a for a in dataset.articles}
        self._db_articles = state.article_by_id()
        self._events = {e.event_id: e for events in state.events.values() for e in events}

    def judge(
        self, item: ResearchReportItem, claim: ResearchClaim
    ) -> tuple[ClaimSupport, DangerousKind | None, str]:
        golden_id = _golden_of(self._identity, item.person_id)
        if claim.claim_type is ResearchClaimType.ROSFINMONITORING_STATUS:
            absence = bool(item.rosfinmonitoring_summary) and str(
                item.rosfinmonitoring_summary
            ).startswith(ABSENCE_WORDING)
            status = item.rosfinmonitoring_status.value if item.rosfinmonitoring_status else None
            if absence and (status != "not_matched" or item.snapshot_id is None):
                return (
                    ClaimSupport.UNSUPPORTED,
                    DangerousKind.UNSUPPORTED_ABSENCE_CLAIM,
                    f"absence worded for status {status}",
                )
        if golden_id is None:
            return ClaimSupport.NOT_EVALUATED, None, "person outside the golden dataset"
        person = self._persons[golden_id]
        if claim.claim_type is ResearchClaimType.IDENTITY:
            return self._citations(person, claim)
        if claim.claim_type is ResearchClaimType.PERSECUTION_CLASSIFICATION:
            return self._persecution(person, item, claim)
        if claim.claim_type is ResearchClaimType.ROSFINMONITORING_STATUS:
            return self._rosfin(person, item)
        return self._event(person, claim)

    def _citation_owners(self, claim: ResearchClaim, golden_id: str) -> tuple[int, int, int]:
        own = other = outside = 0
        for citation in claim.citations:
            db_article = self._db_articles.get(citation.article_id)
            article = self._articles_by_key.get(db_article.key) if db_article else None
            if article is None:
                outside += 1
                continue
            start, end = citation.start_offset, citation.end_offset
            mentions_here = [m for m in article.mentions if m.overlaps(start, end)]
            events_here = [e for e in article.events if e.evidence.overlaps(start, end)]
            if any(m.golden_person_id == golden_id for m in mentions_here) or any(
                golden_id in e.person_ids for e in events_here
            ):
                own += 1
            elif mentions_here:
                other += 1
            else:
                outside += 1
        return own, other, outside

    def _citations(
        self, person: GoldenPerson, claim: ResearchClaim
    ) -> tuple[ClaimSupport, DangerousKind | None, str]:
        if not claim.citations:
            return ClaimSupport.UNSUPPORTED, None, "claim without citation"
        own, other, outside = self._citation_owners(claim, person.golden_person_id)
        if other and not own:
            return (
                ClaimSupport.CONTRADICTED,
                DangerousKind.CROSS_PERSON_EVIDENCE,
                "citations belong to another person",
            )
        if own and not other:
            return ClaimSupport.SUPPORTED, None, ""
        if own:
            return ClaimSupport.PARTIALLY_SUPPORTED, None, "some citations are about others"
        return ClaimSupport.NOT_EVALUATED, None, f"{outside} citations outside annotations"

    def _persecution(
        self, person: GoldenPerson, item: ResearchReportItem, claim: ResearchClaim
    ) -> tuple[ClaimSupport, DangerousKind | None, str]:
        decision = person.persecution
        status = item.persecution_status.value if item.persecution_status else None
        if decision is None or status is None:
            return ClaimSupport.NOT_EVALUATED, None, "no persecution annotation"
        if status in decision.accepted:
            return (
                (ClaimSupport.SUPPORTED, None, "")
                if claim.citations
                else (ClaimSupport.PARTIALLY_SUPPORTED, None, "status right, no citation")
            )
        # Contradiction: the opposite definite status. A definite claim where the
        # annotation is itself undecided goes beyond the evidence: unsupported.
        opposite = {"political": "non_political", "non_political": "political"}.get(status)
        support = (
            ClaimSupport.CONTRADICTED
            if opposite == decision.expected_status.value
            else ClaimSupport.UNSUPPORTED
        )
        if status == "political":
            return (
                support,
                DangerousKind.FALSE_POLITICAL_CLASSIFICATION,
                f"political claimed, annotated {sorted(decision.accepted)}",
            )
        if status in ("uncertain", "needs_review"):
            return ClaimSupport.PARTIALLY_SUPPORTED, None, f"review status {status}"
        return support, None, f"{status} claimed, annotated {sorted(decision.accepted)}"

    def _rosfin(
        self, person: GoldenPerson, item: ResearchReportItem
    ) -> tuple[ClaimSupport, DangerousKind | None, str]:
        decision = person.rosfinmonitoring
        status = item.rosfinmonitoring_status.value if item.rosfinmonitoring_status else None
        if decision is None or status is None:
            return ClaimSupport.NOT_EVALUATED, None, "no RF annotation"
        expected = decision.expected_status.value
        if status == expected:
            return ClaimSupport.SUPPORTED, None, ""
        if status == "not_matched" and decision.truly_listed:
            return (
                ClaimSupport.CONTRADICTED,
                DangerousKind.FALSE_RF_NOT_MATCHED,
                f"listed as {decision.expected_entry}",
            )
        if status in ("ambiguous", "needs_review", "insufficient_data", "no_match_record"):
            return ClaimSupport.PARTIALLY_SUPPORTED, None, f"{status}, annotated {expected}"
        if status == "matched" and expected == RosfinExpectedStatus.NOT_MATCHED.value:
            return ClaimSupport.CONTRADICTED, None, "matched claimed for an unlisted person"
        return ClaimSupport.UNSUPPORTED, None, f"{status}, annotated {expected}"

    def _event(
        self, person: GoldenPerson, claim: ResearchClaim
    ) -> tuple[ClaimSupport, DangerousKind | None, str]:
        event = self._events.get(claim.event_id) if claim.event_id is not None else None
        if event is None:
            return ClaimSupport.NOT_EVALUATED, None, "event not found"
        article = self._articles_by_key.get(event.article_key)
        if article is None:
            return ClaimSupport.NOT_EVALUATED, None, "event article outside the golden dataset"
        matching = [
            e
            for e in article.events
            if e.event_type.value == event.event_type
            and e.evidence.overlaps(event.start, event.end)
        ]
        if any(person.golden_person_id in e.person_ids for e in matching):
            return ClaimSupport.SUPPORTED, None, ""
        if matching:
            return (
                ClaimSupport.CONTRADICTED,
                DangerousKind.CROSS_PERSON_EVIDENCE,
                "the annotated event is about other persons",
            )
        return (
            ClaimSupport.UNSUPPORTED,
            DangerousKind.UNSUPPORTED_EVENT_CLAIM,
            f"no annotated {event.event_type} event at this span",
        )


def _claim_value(item: ResearchReportItem, claim: ResearchClaim) -> str:
    if claim.claim_type is ResearchClaimType.PERSECUTION_CLASSIFICATION and item.persecution_status:
        return item.persecution_status.value
    if (
        claim.claim_type is ResearchClaimType.ROSFINMONITORING_STATUS
        and item.rosfinmonitoring_status
    ):
        return item.rosfinmonitoring_status.value
    return claim.text


def evaluate_research(
    *,
    cases: Sequence[ResearchQueryCase],
    dataset: GoldenDataset,
    state: PipelineState,
    identity: IdentityMap,
    session_factory: sessionmaker[Session],
    snapshot_id: int | None,
    failures: list[Failure],
    llm_parser: ResearchRequestParser | None,
    llm_not_run_reason: str | None,
    semantic_available: bool,
    graph_extras: Callable[[], dict[str, Any]] | None = None,
) -> ResearchSection:
    section = ResearchSection(status=SectionStatus.NOT_RUN, queries=len(cases))
    if not cases:
        return section
    section.by_class = dict(Counter(case.query_class for case in cases))
    judge = ClaimJudge(dataset, state, identity)
    persons = Counts()
    claims: Counter[str] = Counter()
    dangerous: Counter[str] = Counter()
    contradicted_keys: set[tuple[str | None, str, str]] = set()
    executed = 0

    def graph(parser: ResearchRequestParser) -> Any:
        return build_research_graph(
            request_parser=parser,
            research_service=ResearchService(
                repository=SqlAlchemyPersonResearchRepository(session_factory),
                candidate_query=CandidateQueryService(session_factory),
            ),
            snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory),
            planner=ResearchPlanner(SOURCES),
            **(graph_extras() if graph_extras else {}),
        )

    if llm_parser is not None:
        section.intake_status = SectionStatus.RUN
        for case in cases:
            result = run_research_query(graph(llm_parser), case.text)
            _judge_intake(case, result, snapshot_id, section, failures)
    else:
        section.intake_not_run_reason = llm_not_run_reason

    for case in cases:
        if case.expected_request is None:
            continue
        if case.requires_semantic and not semantic_available:
            continue
        executed += 1
        request = _substitute(case.expected_request, snapshot_id)
        result = run_research_query(graph(PreparedRequestParser(request)), case.text)
        if result.status is WorkflowStatus.COMPLETED:
            section.workflow_completed += 1
        elif result.status is WorkflowStatus.CLARIFICATION_REQUIRED:
            section.workflow_clarification += 1
        else:
            section.workflow_failed += 1
            failures.append(
                Failure(
                    component=ErrorComponent.REPORT,
                    severity=Severity.S1,
                    kind="research_workflow_failed",
                    detail=f"{case.query_id}: {result.error.code.value if result.error else result.status.value}",
                )
            )
        returned = {
            golden
            for r in result.results
            if r.person.id is not None and (golden := _golden_of(identity, r.person.id))
        }
        expected = set(case.expected_persons)
        persons.tp += len(expected & returned)
        persons.fn += len(expected - returned)
        if case.expected_persons_exhaustive:
            persons.fp += len(returned - expected)
        for missing in sorted(expected - returned):
            failures.append(
                Failure(
                    component=ErrorComponent.REPORT,
                    severity=Severity.S2,
                    kind="research_person_missing",
                    detail=f"{case.query_id}: expected person not returned",
                    golden_person_id=missing,
                )
            )
        report = result.report
        present: set[tuple[str, str, str]] = set()
        for item in report.items if report else []:
            golden_id = _golden_of(identity, item.person_id)
            for claim in item.claims:
                support, kind, detail = judge.judge(item, claim)
                claims[support.value] += 1
                if golden_id is not None:
                    present.add((claim.claim_type.value, golden_id, _claim_value(item, claim)))
                if kind is not None and support in (
                    ClaimSupport.CONTRADICTED,
                    ClaimSupport.UNSUPPORTED,
                ):
                    dangerous[kind.value] += 1
                if support is ClaimSupport.CONTRADICTED:
                    contradicted_keys.add(
                        (golden_id, claim.claim_type.value, _claim_value(item, claim))
                    )
                if support in (ClaimSupport.CONTRADICTED, ClaimSupport.UNSUPPORTED):
                    failures.append(
                        Failure(
                            component=ErrorComponent.REPORT,
                            severity=Severity.S0
                            if kind is not None or support is ClaimSupport.CONTRADICTED
                            else Severity.S1,
                            kind=f"claim_{support.value.lower()}",
                            detail=f"{case.query_id}: {claim.claim_type.value}: {detail}",
                            golden_person_id=golden_id,
                            evidence=claim.text[:200],
                            dangerous_kind=kind,
                        )
                    )
        if case.report is not None:
            for expectation in case.report.required_claims:
                if (
                    expectation.claim_type,
                    expectation.golden_person_id,
                    expectation.value,
                ) not in present:
                    section.required_claims_missing += 1
            for expectation in case.report.forbidden_claims:
                if (
                    expectation.claim_type,
                    expectation.golden_person_id,
                    expectation.value,
                ) in present:
                    section.forbidden_claims_present += 1
                    failures.append(
                        Failure(
                            component=ErrorComponent.REPORT,
                            severity=Severity.S0,
                            kind="forbidden_claim_present",
                            detail=f"{case.query_id}: {expectation.claim_type}={expectation.value}",
                            golden_person_id=expectation.golden_person_id,
                        )
                    )
    evaluated_claims = sum(v for k, v in claims.items() if k != ClaimSupport.NOT_EVALUATED.value)
    section.status = SectionStatus.RUN if executed else SectionStatus.NOT_RUN
    section.persons = persons.summary()
    section.claims = {support.value: claims.get(support.value, 0) for support in ClaimSupport}
    section.supported_claims_rate = rate(
        claims.get(ClaimSupport.SUPPORTED.value, 0), evaluated_claims
    )
    section.contradicted_claims = claims.get(ClaimSupport.CONTRADICTED.value, 0)
    section.contradicted_claims_unique = len(contradicted_keys)
    section.unsupported_rf_absence_claims = dangerous.get(
        DangerousKind.UNSUPPORTED_ABSENCE_CLAIM.value, 0
    )
    section.dangerous = {kind.value: dangerous.get(kind.value, 0) for kind in DangerousKind}
    return section


def _judge_intake(
    case: ResearchQueryCase,
    result: ResearchQueryResult,
    snapshot_id: int | None,
    section: ResearchSection,
    failures: list[Failure],
) -> None:
    """Correct request, clarification when needed, never a silent reinterpretation."""
    if case.expect_clarification:
        section.intake_clarification_expected += 1
    asked = result.status is WorkflowStatus.CLARIFICATION_REQUIRED
    flagged = {criterion.criterion for criterion in result.unsupported_criteria}
    if case.expect_clarification and asked:
        section.intake_clarification_given += 1
    if case.expected_request is not None and result.request is not None:
        expected = _substitute(case.expected_request, snapshot_id)
        actual = result.request.model_dump(mode="json", exclude_defaults=True)
        if _criteria(actual) == _criteria(expected):
            section.intake_correct += 1
        else:
            failures.append(
                Failure(
                    component=ErrorComponent.RESEARCH_INTAKE,
                    severity=Severity.S1,
                    kind="intake_incorrect_criteria",
                    detail=f"{case.query_id}: {_criteria(actual)} != {_criteria(expected)}",
                )
            )
    silent = (
        (case.expect_clarification or case.unsupported_criteria)
        and result.status is WorkflowStatus.COMPLETED
        and not asked
        and not (set(case.unsupported_criteria) and flagged)
    )
    if silent:
        section.dangerous_silent_reinterpretation += 1
        failures.append(
            Failure(
                component=ErrorComponent.RESEARCH_INTAKE,
                severity=Severity.S0,
                kind="silent_reinterpretation",
                detail=f"{case.query_id}: executed without clarification or unsupported criteria",
            )
        )


def _criteria(request: dict[str, Any]) -> dict[str, Any]:
    criteria = dict(request.get("criteria", {}))
    return {key: value for key, value in sorted(criteria.items()) if value is not None}

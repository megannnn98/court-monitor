"""Component metrics: golden annotations vs the pipeline state after a corpus run.

Golden persons are mapped to canonical persons only through their annotated
mention spans. A canonical person holding mentions of two golden persons that
are not `same_as` each other is a false identity link, wherever it happened.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, sessionmaker

from candidates.service import CandidateQueryService
from evaluation.final.models import Counts
from evaluation.real_world.db_state import DbMention, PipelineState
from evaluation.real_world.golden import (
    DangerousKind,
    GoldenArticle,
    GoldenDataset,
    GoldenPerson,
    RosfinExpectedStatus,
    TextSpan,
)
from evaluation.real_world.metrics import Span, confusion_matrix, match_spans, rate
from evaluation.real_world.results import (
    CandidateSection,
    EntityResolutionSection,
    ErrorComponent,
    EvidenceSection,
    ExtractionSection,
    Failure,
    PersecutionSection,
    RosfinSection,
    SectionStatus,
    Severity,
)
from persecution.models import PersecutionClassificationStatus
from research.models import MAX_RESEARCH_LIMIT, ResearchRequest
from research.service import ResearchService
from research.unit_of_work import SqlAlchemyResearchUnitOfWork

POLITICAL = PersecutionClassificationStatus.POLITICAL.value
NON_POLITICAL = PersecutionClassificationStatus.NON_POLITICAL.value
REVIEW_PERSECUTION = {
    PersecutionClassificationStatus.UNCERTAIN.value,
    PersecutionClassificationStatus.NEEDS_REVIEW.value,
}
RF_REVIEW = {RosfinExpectedStatus.AMBIGUOUS.value, RosfinExpectedStatus.NEEDS_REVIEW.value}
RF_LABELS = [
    "matched",
    "not_matched",
    "ambiguous",
    "needs_review",
    "insufficient_data",
    "no_match_record",
    "no_snapshot",
    "unmapped_person",
]
PERSECUTION_LABELS = [status.value for status in PersecutionClassificationStatus] + [
    "not_classified",
    "unmapped_person",
]
RECALL_KS = (1, 5)
PENDING_REVIEW = "pending_review"


@dataclass
class IdentityMap:
    """Golden person <-> canonical person, derived from annotated mention spans."""

    # golden mention (case_id, index) -> matched pipeline mention
    mention_matches: dict[tuple[str, int], DbMention] = field(default_factory=dict)
    persons_of_golden: dict[str, list[int]] = field(default_factory=dict)
    golden_of_person: dict[int, set[str]] = field(default_factory=dict)
    # canonical person -> golden persons that are different real people
    false_link_persons: dict[int, set[str]] = field(default_factory=dict)
    true_person: dict[str, int | None] = field(default_factory=dict)

    def mapped_person(self, golden_person_id: str) -> int | None:
        """The canonical person that stands for this golden person, if unambiguous."""
        person = self.true_person.get(golden_person_id)
        if person is None or person in self.false_link_persons:
            return None
        return person


def same_as_components(persons: Sequence[GoldenPerson]) -> dict[str, str]:
    """Golden person -> representative of its same_as group (transitive, symmetric)."""
    parent = {person.golden_person_id: person.golden_person_id for person in persons}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for person in persons:
        for other in person.same_as:
            if other in parent:
                first, second = find(person.golden_person_id), find(other)
                if first != second:
                    parent[max(first, second)] = min(first, second)
    return {key: find(key) for key in parent}


def build_identity_map(dataset: GoldenDataset, state: PipelineState) -> IdentityMap:
    identity = IdentityMap()
    person_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for article in dataset.articles:
        db_mentions = state.mentions.get(article.key, [])
        pairs = match_spans(
            [Span(m.start, m.end) for m in article.mentions],
            [Span(m.start, m.end) for m in db_mentions],
            same_label=False,
        )
        for gold_index, db_index in pairs:
            db_mention = db_mentions[db_index]
            identity.mention_matches[(article.case_id, gold_index)] = db_mention
            golden_id = article.mentions[gold_index].golden_person_id
            if db_mention.person_id is not None:
                person_counts[golden_id][db_mention.person_id] += 1
                identity.golden_of_person.setdefault(db_mention.person_id, set()).add(golden_id)
    for golden_id, counts in person_counts.items():
        ordered = sorted(counts, key=lambda person: (-counts[person], person))
        identity.persons_of_golden[golden_id] = ordered
        identity.true_person[golden_id] = ordered[0] if ordered else None
    group = same_as_components(dataset.persons)
    for person_id, golden_ids in identity.golden_of_person.items():
        distinct = {
            golden_id
            for golden_id in golden_ids
            if any(
                group.get(other, other) != group.get(golden_id, golden_id) for other in golden_ids
            )
        }
        if distinct:
            identity.false_link_persons[person_id] = distinct
    return identity


def _failure(
    component: ErrorComponent,
    severity: Severity,
    kind: str,
    detail: str,
    *,
    article: GoldenArticle | None = None,
    golden_person_id: str | None = None,
    evidence: str | None = None,
    dangerous_kind: DangerousKind | None = None,
) -> Failure:
    gated = not (
        article is not None
        and dangerous_kind is not None
        and dangerous_kind in article.known_limitation_kinds
    )
    return Failure(
        component=component,
        severity=severity,
        kind=kind,
        detail=detail,
        case_id=article.case_id if article else None,
        article=article.key if article else None,
        golden_person_id=golden_person_id,
        evidence=evidence,
        dangerous_kind=dangerous_kind,
        gated=gated,
    )


# -- extraction ------------------------------------------------------------------------


def evaluate_extraction(
    dataset: GoldenDataset, state: PipelineState, identity: IdentityMap, failures: list[Failure]
) -> ExtractionSection:
    mentions = Counts()
    events = Counts()
    historical = Counts()
    matched_events = 0
    association_correct = 0
    cross_person_links = 0
    evaluated = 0
    for article in dataset.articles:
        if article.key not in state.articles:
            failures.append(
                _failure(
                    ErrorComponent.INFRASTRUCTURE,
                    Severity.S1,
                    "article_not_ingested",
                    "golden article is missing from the pipeline database",
                    article=article,
                )
            )
            continue
        evaluated += 1
        db_mentions = state.mentions.get(article.key, [])
        matched_gold = {gi for (case, gi) in identity.mention_matches if case == article.case_id}
        matched_db = {
            identity.mention_matches[(article.case_id, gi)].mention_id for gi in matched_gold
        }
        mentions.tp += len(matched_gold)
        mentions.fn += len(article.mentions) - len(matched_gold)
        mentions.fp += sum(1 for m in db_mentions if m.mention_id not in matched_db)
        for index, gold in enumerate(article.mentions):
            if index not in matched_gold:
                failures.append(
                    _failure(
                        ErrorComponent.EXTRACTION,
                        Severity.S2,
                        "person_mention_missed",
                        f"mention {gold.text!r} at {gold.start} not extracted",
                        article=article,
                        golden_person_id=gold.golden_person_id,
                        evidence=gold.text,
                    )
                )
        for db_mention in db_mentions:
            if db_mention.mention_id not in matched_db:
                failures.append(
                    _failure(
                        ErrorComponent.EXTRACTION,
                        Severity.S2,
                        "spurious_person_mention",
                        f"extracted {db_mention.surface!r} at {db_mention.start} is not a person",
                        article=article,
                        evidence=db_mention.surface,
                    )
                )

        db_events = state.events.get(article.key, [])
        pairs = match_spans(
            [Span(e.evidence.start, e.evidence.end, e.event_type.value) for e in article.events],
            [Span(e.start, e.end, e.event_type) for e in db_events],
        )
        events.tp += len(pairs)
        events.fn += len(article.events) - len(pairs)
        events.fp += len(db_events) - len(pairs)
        matched_gold_events = {gi for gi, _ in pairs}
        for index, gold_event in enumerate(article.events):
            if gold_event.historical:
                historical.add(expected=True, actual=index in matched_gold_events)
            if index not in matched_gold_events:
                failures.append(
                    _failure(
                        ErrorComponent.EXTRACTION,
                        Severity.S2,
                        "event_missed",
                        f"{gold_event.event_type.value} event {gold_event.event_id} not extracted",
                        article=article,
                        evidence=gold_event.evidence.text[:200],
                    )
                )
        golden_by_person: dict[int, set[str]] = defaultdict(set)
        for (case, gi), db_mention in identity.mention_matches.items():
            if case == article.case_id and db_mention.person_id is not None:
                golden_by_person[db_mention.person_id].add(article.mentions[gi].golden_person_id)
        for gi, di in pairs:
            matched_events += 1
            gold_event = article.events[gi]
            linked = (
                set().union(
                    *(golden_by_person.get(p, {f"person:{p}"}) for p in db_events[di].person_ids)
                )
                if db_events[di].person_ids
                else set()
            )
            expected = set(gold_event.person_ids)
            if linked == expected:
                association_correct += 1
                continue
            wrong = sorted(g for g in linked - expected if not g.startswith("person:"))
            cross_person_links += len(wrong)
            failures.append(
                _failure(
                    ErrorComponent.EVENT_ASSOCIATION,
                    Severity.S1 if wrong else Severity.S2,
                    "event_person_association",
                    f"event {gold_event.event_id} linked to {sorted(linked)}, expected {sorted(expected)}",
                    article=article,
                    evidence=gold_event.evidence.text[:200],
                )
            )
    return ExtractionSection(
        status=SectionStatus.RUN if evaluated else SectionStatus.NOT_RUN,
        person_mentions=mentions.summary(),
        events=events.summary(),
        historical_events=historical.summary(),
        events_matched=matched_events,
        events_association_correct=association_correct,
        person_event_association_accuracy=rate(association_correct, matched_events),
        cross_person_event_links=cross_person_links,
    )


# -- entity resolution ------------------------------------------------------------------


def evaluate_entity_resolution(
    dataset: GoldenDataset, identity: IdentityMap, failures: list[Failure]
) -> EntityResolutionSection:
    section = EntityResolutionSection(status=SectionStatus.NOT_RUN)
    hits = dict.fromkeys(RECALL_KS, 0)
    link_expected = 0
    auto_links = correct = reviews = false_create = unresolved = evaluated = 0
    articles = {article.case_id: article for article in dataset.articles}
    for (case_id, gi), db_mention in sorted(
        identity.mention_matches.items(), key=lambda item: item[1].mention_id
    ):
        article = articles.get(case_id)
        if article is None:
            continue
        golden_id = article.mentions[gi].golden_person_id
        evaluated += 1
        decision = db_mention.decision
        truth = identity.true_person.get(golden_id)
        if decision is None:
            unresolved += 1
            continue
        if decision.status == PENDING_REVIEW:
            reviews += 1
        creator = decision.action == "create_new" and db_mention.person_id == truth
        if truth is not None and not creator:
            link_expected += 1
            for k in RECALL_KS:
                hits[k] += truth in decision.candidate_person_ids[:k]
        if decision.action == "auto_link":
            auto_links += 1
            if decision.selected_person_id == truth and truth not in identity.false_link_persons:
                correct += 1
        if decision.action == "create_new" and truth is not None and not creator:
            false_create += 1
            failures.append(
                _failure(
                    ErrorComponent.ENTITY_RESOLUTION,
                    Severity.S2,
                    "false_create_new",
                    f"{db_mention.surface!r} created a new person although the person existed",
                    article=article,
                    golden_person_id=golden_id,
                    evidence=db_mention.surface,
                )
            )
    for person_id, golden_ids in sorted(identity.false_link_persons.items()):
        for golden_id in sorted(golden_ids):
            article = next(iter(dataset.articles_of(golden_id)), None)
            failures.append(
                _failure(
                    ErrorComponent.ENTITY_RESOLUTION,
                    Severity.S0,
                    "false_person_link",
                    f"one canonical person holds different real people {sorted(golden_ids)}",
                    article=article,
                    golden_person_id=golden_id,
                    dangerous_kind=DangerousKind.FALSE_PERSON_LINK,
                )
            )
    duplicates = sum(max(0, len(persons) - 1) for persons in identity.persons_of_golden.values())
    for golden_id, persons in sorted(identity.persons_of_golden.items()):
        if len(persons) > 1:
            failures.append(
                _failure(
                    ErrorComponent.ENTITY_RESOLUTION,
                    Severity.S2,
                    "duplicate_canonical_person",
                    f"{golden_id} is split over {len(persons)} canonical persons",
                    golden_person_id=golden_id,
                )
            )
    section.status = SectionStatus.RUN if evaluated else SectionStatus.NOT_RUN
    section.mentions_evaluated = evaluated
    section.link_expected_mentions = link_expected
    section.candidate_recall_at_1 = rate(hits[1], link_expected)
    section.candidate_recall_at_5 = rate(hits[5], link_expected)
    section.auto_links = auto_links
    section.correct_auto_links = correct
    section.auto_link_precision = rate(correct, auto_links)
    section.auto_link_recall = rate(correct, link_expected)
    section.reviews = reviews
    section.review_rate = rate(reviews, evaluated)
    section.false_links = sum(len(g) for g in identity.false_link_persons.values())
    section.false_create_new = false_create
    section.duplicate_canonical_persons = duplicates
    section.unresolved_mentions = unresolved
    return section


# -- persecution, Rosfinmonitoring, candidates ----------------------------------------------


def _articles_of(dataset: GoldenDataset, golden_id: str) -> list[GoldenArticle]:
    return dataset.articles_of(golden_id)


def evaluate_persecution(
    dataset: GoldenDataset, state: PipelineState, identity: IdentityMap, failures: list[Failure]
) -> PersecutionSection:
    pairs: list[tuple[str, str]] = []
    political = Counts()
    non_political = Counts()
    correct = review = unmapped = cross_person = 0
    expected_political = {
        p.golden_person_id
        for p in dataset.persons
        if p.persecution and p.persecution.expected_status.value == POLITICAL
    }
    for person in dataset.persons:
        decision = person.persecution
        if decision is None:
            continue
        person_id = identity.mapped_person(person.golden_person_id)
        if person_id is None:
            unmapped += 1
            actual = "unmapped_person"
        else:
            actual = state.classifications.get(person_id, "not_classified")
        expected = decision.expected_status.value
        pairs.append((expected, actual))
        accepted = actual in decision.accepted
        correct += accepted
        review += actual in REVIEW_PERSECUTION
        political.add(expected=expected == POLITICAL, actual=actual == POLITICAL)
        non_political.add(expected=expected == NON_POLITICAL, actual=actual == NON_POLITICAL)
        articles = _articles_of(dataset, person.golden_person_id)
        article = articles[0] if articles else None
        if accepted:
            continue
        if actual == POLITICAL:
            neighbours = {
                m.golden_person_id
                for a in articles
                for m in a.mentions
                if m.golden_person_id != person.golden_person_id
            }
            is_cross = bool(neighbours & expected_political)
            cross_person += is_cross
            failures.append(
                _failure(
                    ErrorComponent.PERSECUTION,
                    Severity.S0,
                    "cross_person_political_attribution" if is_cross else "false_political",
                    f"classified political, expected {sorted(decision.accepted)}"
                    + (
                        f"; political neighbours {sorted(neighbours & expected_political)}"
                        if is_cross
                        else ""
                    ),
                    article=article,
                    golden_person_id=person.golden_person_id,
                    dangerous_kind=(
                        DangerousKind.CROSS_PERSON_EVIDENCE
                        if is_cross
                        else DangerousKind.FALSE_POLITICAL_CLASSIFICATION
                    ),
                )
            )
        else:
            failures.append(
                _failure(
                    ErrorComponent.PERSECUTION,
                    Severity.S2
                    if actual in REVIEW_PERSECUTION or actual == "unmapped_person"
                    else Severity.S1,
                    "persecution_status",
                    f"classified {actual}, expected {sorted(decision.accepted)}",
                    article=article,
                    golden_person_id=person.golden_person_id,
                )
            )
    return PersecutionSection(
        status=SectionStatus.RUN if pairs else SectionStatus.NOT_RUN,
        evaluated=len(pairs),
        unmapped_persons=unmapped,
        accuracy=rate(correct, len(pairs)),
        political=political.summary(),
        non_political=non_political.summary(),
        uncertain_or_review_rate=rate(review, len(pairs)),
        confusion_matrix=confusion_matrix(pairs, PERSECUTION_LABELS) if pairs else {},
        cross_person_political_attribution=cross_person,
    )


def evaluate_rosfinmonitoring(
    dataset: GoldenDataset,
    state: PipelineState,
    identity: IdentityMap,
    snapshot_id: int | None,
    failures: list[Failure],
) -> RosfinSection:
    pairs: list[tuple[str, str]] = []
    correct = false_not_matched = review_instead = 0
    for person in dataset.persons:
        decision = person.rosfinmonitoring
        if decision is None:
            continue
        person_id = identity.mapped_person(person.golden_person_id)
        if snapshot_id is None:
            actual = "no_snapshot"
        elif person_id is None:
            actual = "unmapped_person"
        else:
            actual = state.rf_matches.get(person_id, "no_match_record")
        expected = decision.expected_status.value
        pairs.append((expected, actual))
        articles = _articles_of(dataset, person.golden_person_id)
        article = articles[0] if articles else None
        if actual == expected:
            correct += 1
            continue
        if decision.acceptable_review and actual in RF_REVIEW:
            correct += 1
            review_instead += 1
            continue
        if actual == "not_matched" and decision.truly_listed:
            false_not_matched += 1
            failures.append(
                _failure(
                    ErrorComponent.ROSFINMONITORING,
                    Severity.S0,
                    "false_rf_not_matched",
                    f"listed as {decision.expected_entry!r} in the snapshot, matcher said not_matched",
                    article=article,
                    golden_person_id=person.golden_person_id,
                    dangerous_kind=DangerousKind.FALSE_RF_NOT_MATCHED,
                )
            )
            continue
        failures.append(
            _failure(
                ErrorComponent.ROSFINMONITORING,
                Severity.S1 if actual in ("matched", "not_matched") else Severity.S2,
                "rf_status",
                f"RF status {actual}, expected {expected}",
                article=article,
                golden_person_id=person.golden_person_id,
            )
        )
    return RosfinSection(
        status=SectionStatus.RUN if pairs else SectionStatus.NOT_RUN,
        snapshot_id=dataset.version.rf_snapshot_id,
        evaluated=len(pairs),
        accuracy=rate(correct, len(pairs)),
        confusion_matrix=confusion_matrix(pairs, RF_LABELS) if pairs else {},
        false_not_matched=false_not_matched,
        review_instead_of_expected=review_instead,
    )


def evaluate_candidates(
    dataset: GoldenDataset,
    state: PipelineState,
    identity: IdentityMap,
    session_factory: sessionmaker[Session],
    snapshot_id: int | None,
    failures: list[Failure],
) -> CandidateSection:
    if snapshot_id is None:
        return CandidateSection(status=SectionStatus.NOT_RUN)
    candidate_ids = {
        candidate.person_id
        for candidate in CandidateQueryService(session_factory)
        .get_candidates(snapshot_id, limit=None)
        .candidates
    }
    candidates = Counts()
    findings = Counts()
    checked: list[tuple[GoldenPerson, int]] = []
    for person in dataset.persons:
        if person.candidate is None:
            continue
        expected = person.candidate.expected_main_candidate
        person_id = identity.mapped_person(person.golden_person_id)
        actual = person_id is not None and person_id in candidate_ids
        finding = person_id is not None and person_id in state.active_findings
        candidates.add(expected=expected, actual=actual)
        findings.add(expected=expected, actual=finding)
        articles = _articles_of(dataset, person.golden_person_id)
        article = articles[0] if articles else None
        if actual and person_id is not None:
            checked.append((person, person_id))
        if actual != expected:
            failures.append(
                _failure(
                    ErrorComponent.PERSECUTION,
                    Severity.S1 if actual else Severity.S2,
                    "candidate_false_positive" if actual else "candidate_missed",
                    f"main candidate expected={expected}, CandidateQuery={actual}",
                    article=article,
                    golden_person_id=person.golden_person_id,
                )
            )
        if finding and not expected:
            failures.append(
                _failure(
                    ErrorComponent.MONITORING,
                    Severity.S0,
                    "false_actionable_finding",
                    "active monitoring finding for a person who is not a main candidate",
                    article=article,
                    golden_person_id=person.golden_person_id,
                    dangerous_kind=DangerousKind.FALSE_ACTIONABLE_CANDIDATE,
                )
            )
    return CandidateSection(
        status=SectionStatus.RUN
        if candidates.tp + candidates.fp + candidates.fn + candidates.tn
        else SectionStatus.NOT_RUN,
        snapshot_id=dataset.version.rf_snapshot_id,
        candidates=candidates.summary(),
        findings=findings.summary(),
        evidence=evaluate_evidence(dataset, state, session_factory, snapshot_id, checked, failures),
    )


def _covers(spans: Sequence[TextSpan], start: int, end: int) -> bool:
    return any(span.overlaps(start, end) for span in spans)


def evaluate_evidence(
    dataset: GoldenDataset,
    state: PipelineState,
    session_factory: sessionmaker[Session],
    snapshot_id: int,
    checked: Sequence[tuple[GoldenPerson, int]],
    failures: list[Failure],
) -> EvidenceSection:
    """Evidence the research layer returns for each actual main candidate."""
    service = ResearchService(
        unit_of_work=SqlAlchemyResearchUnitOfWork(session_factory),
    )
    articles_by_id = state.article_by_id()
    golden_by_key = {article.key: article for article in dataset.articles}
    with_evidence = spans_total = relevant = relevant_total = supports = 0
    traceable = offset_valid = 0
    for person, person_id in checked:
        response = service.execute(
            ResearchRequest.model_validate(
                {
                    "object_type": "person",
                    "criteria": {"person_id": person_id, "snapshot_id": snapshot_id},
                    "limit": MAX_RESEARCH_LIMIT,
                }
            )
        )
        results = response.results
        evidence = results[0].evidence if results else []
        sources = {source.article_id for source in results[0].sources} if results else set()
        with_evidence += bool(evidence)
        if not evidence:
            failures.append(
                _failure(
                    ErrorComponent.REPORT,
                    Severity.S1,
                    "candidate_without_evidence",
                    "main candidate has no evidence span",
                    golden_person_id=person.golden_person_id,
                )
            )
        persecution_spans = [
            ref.span for ref in (person.persecution.evidence if person.persecution else [])
        ]
        for item in evidence:
            spans_total += 1
            db_article = articles_by_id.get(item.article_id)
            traceable += item.article_id in sources and db_article is not None
            if (
                db_article is not None
                and db_article.text[item.start_offset : item.end_offset] == item.text
            ):
                offset_valid += 1
            golden_article = golden_by_key.get(db_article.key) if db_article else None
            if golden_article is None:
                continue
            relevant_total += 1
            own = [
                m for m in golden_article.mentions if m.golden_person_id == person.golden_person_id
            ]
            own_events = [
                e.evidence for e in golden_article.events if person.golden_person_id in e.person_ids
            ]
            others = [
                m for m in golden_article.mentions if m.golden_person_id != person.golden_person_id
            ]
            is_own = _covers([*own, *own_events], item.start_offset, item.end_offset)
            relevant += is_own
            supports += _covers(
                [*own_events, *[r for r in persecution_spans]], item.start_offset, item.end_offset
            )
            if not is_own and _covers(others, item.start_offset, item.end_offset):
                failures.append(
                    _failure(
                        ErrorComponent.REPORT,
                        Severity.S0,
                        "cross_person_evidence",
                        f"evidence {item.text[:120]!r} is about another person",
                        article=golden_article,
                        golden_person_id=person.golden_person_id,
                        evidence=item.text[:200],
                        dangerous_kind=DangerousKind.CROSS_PERSON_EVIDENCE,
                    )
                )
    return EvidenceSection(
        candidates_checked=len(checked),
        with_evidence_rate=rate(with_evidence, len(checked)),
        relevant_evidence_rate=rate(relevant, relevant_total),
        supports_classification_rate=rate(supports, relevant_total),
        traceable_rate=rate(traceable, spans_total),
        offset_valid_rate=rate(offset_valid, spans_total),
        evidence_spans=spans_total,
    )


def golden_keys(dataset: GoldenDataset) -> set[str]:
    return {article.key for article in dataset.articles}


def mapping_summary(identity: IdentityMap) -> Mapping[str, int]:
    return {
        "matched_mentions": len(identity.mention_matches),
        "mapped_golden_persons": sum(1 for p in identity.true_person.values() if p is not None),
        "false_link_persons": len(identity.false_link_persons),
    }

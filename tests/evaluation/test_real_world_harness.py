"""Evaluator infrastructure: metrics, identity mapping, dangerous errors, gates, reports.

No database: the pipeline state is built in memory, so every metric and gate
is checked against known counts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evaluation.real_world.component_evaluation import (
    build_identity_map,
    evaluate_entity_resolution,
    evaluate_extraction,
    evaluate_persecution,
    evaluate_rosfinmonitoring,
)
from evaluation.real_world.db_state import DbArticle, DbDecision, DbEvent, DbMention, PipelineState
from evaluation.real_world.golden import DangerousKind, GoldenDataset, excerpt_windows
from evaluation.real_world.metrics import Span, confusion_matrix, match_spans, per_100, rate
from evaluation.real_world.models import sha256_text
from evaluation.real_world.policy import DEFAULT_POLICY_PATH, load_policy
from evaluation.real_world.report import recommended_next_work, render_markdown, write_reports
from evaluation.real_world.research_eval import _substitute
from evaluation.real_world.results import (
    CandidateSection,
    DatasetSummary,
    EntityResolutionSection,
    ErrorComponent,
    ExtractionSection,
    Failure,
    GateKind,
    GateStatus,
    MonitoringSection,
    NamesakeSection,
    OverallStatus,
    PerformanceSection,
    PersecutionSection,
    Provenance,
    RealWorldValidationReport,
    ResearchSection,
    RetrievalSection,
    RosfinSection,
    SectionStatus,
    Severity,
)
from evaluation.real_world.safety import (
    EXIT_GATES_FAILED,
    EXIT_OK,
    GateInputs,
    RealWorldSafetyGateEvaluator,
    gated_count,
    overall_status,
)
from evaluation.real_world.state_snapshot import compare_snapshots, duplicate_counts

TEXT = "Суд арестовал Ивана Петрова. Марию Сидорову отпустили. Иван Петров подал жалобу."
KEY = "ovd-info:/express-news/2026/04/01/case"


def _span(text: str, occurrence: int = 1) -> dict[str, Any]:
    start = -1
    for _ in range(occurrence):
        start = TEXT.index(text, start + 1)
    return {"start": start, "end": start + len(text), "text": text}


def golden(**article_overrides: Any) -> GoldenDataset:
    mentions = [
        {**_span("Ивана Петрова"), "golden_person_id": "gp-petrov"},
        {**_span("Марию Сидорову"), "golden_person_id": "gp-sidorova"},
        {**_span("Иван Петров"), "golden_person_id": "gp-petrov"},
    ]
    events: list[dict[str, Any]] = [
        {
            "event_id": "e1",
            "event_type": "arrest",
            "person_ids": ["gp-petrov"],
            "evidence": _span("Суд арестовал Ивана Петрова."),
        },
        {
            "event_id": "e2",
            "event_type": "release",
            "person_ids": ["gp-sidorova"],
            "evidence": _span("Марию Сидорову отпустили."),
        },
    ]
    article: dict[str, Any] = {
        "case_id": "case",
        "source": "ovd-info",
        "external_id": "/express-news/2026/04/01/case",
        "canonical_url": "https://ovd.info/express-news/2026/04/01/case",
        "published_at": datetime(2026, 4, 1, tzinfo=UTC).isoformat(),
        "content_hash": sha256_text(TEXT),
        "split": "dev",
        "excerpts": [s.model_dump() for s in excerpt_windows(TEXT, [(0, len(TEXT))])],
        "mentions": mentions,
        "events": events,
    }
    article.update(article_overrides)
    rf = {"snapshot": "rf-eval-v1"}
    return GoldenDataset.model_validate(
        {
            "version": {
                "dataset_version": "t",
                "golden_dataset_hash": "",
                "rf_snapshot_id": "rf-eval-v1",
                "rf_snapshot_path": "x.csv",
            },
            "persons": [
                {
                    "golden_person_id": "gp-petrov",
                    "canonical_name": "Иван Петров",
                    "persecution": {"expected_status": "political"},
                    "rosfinmonitoring": {
                        **rf,
                        "expected_status": "matched",
                        "expected_entry": "ПЕТРОВ ИВАН",
                    },
                },
                {
                    "golden_person_id": "gp-sidorova",
                    "canonical_name": "Мария Сидорова",
                    "persecution": {"expected_status": "non_political"},
                    "rosfinmonitoring": {**rf, "expected_status": "not_matched"},
                },
            ],
            "articles": [article],
        }
    )


def state(
    *, person_of_sidorova: int = 2, link_event_to: frozenset[int] | None = None
) -> PipelineState:
    """Pipeline output: Петров = person 1 (created, then auto-linked), Сидорова = given person."""
    s = PipelineState()
    s.articles[KEY] = DbArticle(1, KEY, "t", TEXT, "https://ovd.info/x")
    create = DbDecision("create_new", "applied", None, ())
    link = DbDecision("auto_link", "applied", 1, (1,))
    first = _span("Ивана Петрова")
    second = _span("Марию Сидорову")
    third = _span("Иван Петров")
    s.mentions[KEY] = [
        DbMention(10, KEY, first["start"], first["end"], first["text"], 1, create),
        DbMention(
            11,
            KEY,
            second["start"],
            second["end"],
            second["text"],
            person_of_sidorova,
            link if person_of_sidorova == 1 else create,
        ),
        DbMention(12, KEY, third["start"], third["end"], third["text"], 1, link),
        # Spurious: not a person.
        DbMention(13, KEY, 0, 3, "Суд", None, None),
    ]
    arrest = _span("Суд арестовал Ивана Петрова.")
    s.events[KEY] = [
        DbEvent(20, KEY, "arrest", arrest["start"], arrest["end"], link_event_to or frozenset({1})),
    ]
    s.classifications = {1: "political", 2: "political"}
    s.rf_matches = {1: "not_matched", 2: "not_matched"}
    return s


# -- metrics ------------------------------------------------------------------------------


def test_rates_are_none_for_zero_denominators() -> None:
    assert rate(0, 0) is None
    assert per_100(3, 0) is None
    assert rate(1, 3) == 0.3333
    assert per_100(3, 12) == 25.0


def test_span_matching_is_one_to_one_and_label_aware() -> None:
    gold = [Span(0, 10, "arrest"), Span(20, 30, "release")]
    predicted = [Span(2, 12, "arrest"), Span(1, 9, "arrest"), Span(20, 30, "fine")]
    assert match_spans(gold, predicted) == [(0, 0)]
    assert match_spans(gold, predicted, same_label=False) == [(0, 0), (1, 2)]


def test_confusion_matrix_keeps_zero_rows_and_unknown_labels() -> None:
    matrix = confusion_matrix(
        [("matched", "not_matched"), ("matched", "weird")], ["matched", "ambiguous"]
    )
    assert matrix["ambiguous"] == {"matched": 0, "ambiguous": 0, "not_matched": 0, "weird": 0}
    assert matrix["matched"]["not_matched"] == 1 and matrix["matched"]["weird"] == 1


# -- components ---------------------------------------------------------------------------


def test_extraction_mentions_events_and_association() -> None:
    dataset, pipeline = golden(), state()
    failures: list[Failure] = []
    section = evaluate_extraction(
        dataset, pipeline, build_identity_map(dataset, pipeline), failures
    )

    assert section.person_mentions["tp"] == 3 and section.person_mentions["fp"] == 1
    assert section.events["tp"] == 1 and section.events["fn"] == 1
    assert section.person_event_association_accuracy == 1.0
    assert {f.kind for f in failures} == {"spurious_person_mention", "event_missed"}


def test_event_linked_to_another_golden_person_is_an_association_error() -> None:
    dataset, pipeline = golden(), state(link_event_to=frozenset({1, 2}))
    failures: list[Failure] = []
    section = evaluate_extraction(
        dataset, pipeline, build_identity_map(dataset, pipeline), failures
    )

    assert section.person_event_association_accuracy == 0.0
    assert section.cross_person_event_links == 1
    assert any(f.component is ErrorComponent.EVENT_ASSOCIATION for f in failures)


def test_one_person_holding_two_real_people_is_a_false_link() -> None:
    dataset, pipeline = golden(), state(person_of_sidorova=1)
    identity = build_identity_map(dataset, pipeline)
    failures: list[Failure] = []
    section = evaluate_entity_resolution(dataset, identity, failures)

    assert identity.false_link_persons == {1: {"gp-petrov", "gp-sidorova"}}
    assert section.false_links == 2
    assert section.auto_link_precision == 0.0
    assert gated_count(failures, dangerous=DangerousKind.FALSE_PERSON_LINK) == 2
    # Mapping refuses a merged person: no metric may use it as the golden person's identity.
    assert identity.mapped_person("gp-petrov") is None


def test_correct_links_give_full_precision_and_recall() -> None:
    dataset, pipeline = golden(), state()
    failures: list[Failure] = []
    section = evaluate_entity_resolution(dataset, build_identity_map(dataset, pipeline), failures)
    assert (section.auto_link_precision, section.candidate_recall_at_1, section.false_links) == (
        1.0,
        1.0,
        0,
    )


def test_known_limitation_excuses_only_its_declared_kind() -> None:
    dataset = golden(known_limitation="namesakes", known_limitation_kinds=["false_person_link"])
    pipeline = state(person_of_sidorova=1)
    identity = build_identity_map(dataset, pipeline)
    failures: list[Failure] = []
    evaluate_entity_resolution(dataset, identity, failures)
    evaluate_rosfinmonitoring(dataset, state(), build_identity_map(dataset, state()), 1, failures)

    assert gated_count(failures, dangerous=DangerousKind.FALSE_PERSON_LINK) == 0
    assert any(
        f.dangerous_kind is DangerousKind.FALSE_PERSON_LINK and not f.gated for f in failures
    )
    assert gated_count(failures, dangerous=DangerousKind.FALSE_RF_NOT_MATCHED) == 1


def test_false_not_matched_and_rf_absence_safety() -> None:
    dataset, pipeline = golden(), state()
    failures: list[Failure] = []
    section = evaluate_rosfinmonitoring(
        dataset, pipeline, build_identity_map(dataset, pipeline), 1, failures
    )
    assert section.false_not_matched == 1
    assert section.confusion_matrix["matched"]["not_matched"] == 1

    # Review statuses and a missing snapshot are never counted as NOT_MATCHED.
    for status in ("ambiguous", "needs_review", "insufficient_data"):
        reviewed = state()
        reviewed.rf_matches = {1: status, 2: "not_matched"}
        out: list[Failure] = []
        result = evaluate_rosfinmonitoring(
            dataset, reviewed, build_identity_map(dataset, reviewed), 1, out
        )
        assert result.false_not_matched == 0
    none: list[Failure] = []
    no_snapshot = evaluate_rosfinmonitoring(
        dataset, pipeline, build_identity_map(dataset, pipeline), None, none
    )
    assert no_snapshot.false_not_matched == 0
    assert no_snapshot.confusion_matrix["not_matched"]["no_snapshot"] == 1


def test_political_neighbour_makes_a_false_political_a_cross_person_attribution() -> None:
    dataset, pipeline = golden(), state()
    failures: list[Failure] = []
    section = evaluate_persecution(
        dataset, pipeline, build_identity_map(dataset, pipeline), failures
    )

    assert section.cross_person_political_attribution == 1
    assert section.political == {
        "tp": 1,
        "fp": 1,
        "fn": 0,
        "precision": 0.5,
        "recall": 1.0,
        "f1": 0.6667,
    }
    assert [f.severity for f in failures] == [Severity.S0]


# -- gates, status, report ----------------------------------------------------------------


def _inputs(failures: list[Failure], **sections: Any) -> GateInputs:
    defaults: dict[str, Any] = {
        "extraction": ExtractionSection(status=SectionStatus.NOT_RUN),
        "entity_resolution": EntityResolutionSection(status=SectionStatus.RUN),
        "persecution": PersecutionSection(status=SectionStatus.RUN),
        "candidate_query": CandidateSection(status=SectionStatus.NOT_RUN),
        "retrieval": RetrievalSection(status=SectionStatus.NOT_RUN),
        "research": ResearchSection(status=SectionStatus.NOT_RUN),
        "monitoring": MonitoringSection(status=SectionStatus.NOT_RUN),
    }
    defaults.update(sections)
    return GateInputs(failures=failures, review_workload_per_100=None, **defaults)


def _failure(kind: DangerousKind, *, gated: bool = True) -> Failure:
    return Failure(
        component=ErrorComponent.ENTITY_RESOLUTION,
        severity=Severity.S0,
        kind=kind.value,
        detail="x",
        dangerous_kind=kind,
        gated=gated,
    )


def test_hard_gate_fails_on_one_gated_error_and_not_run_is_never_pass() -> None:
    policy, _ = load_policy(DEFAULT_POLICY_PATH)
    gates = {
        g.name: g
        for g in RealWorldSafetyGateEvaluator(policy).evaluate(
            _inputs([_failure(DangerousKind.FALSE_PERSON_LINK)])
        )
    }
    assert gates["false_person_auto_link"].status is GateStatus.FAIL
    assert gates["contradicted_report_claims"].status is GateStatus.NOT_RUN
    assert gates["semantic_recall_at_5"].status is GateStatus.NOT_RUN
    assert gates["false_person_auto_link"].kind is GateKind.HARD

    excused = {
        g.name: g
        for g in RealWorldSafetyGateEvaluator(policy).evaluate(
            _inputs([_failure(DangerousKind.FALSE_PERSON_LINK, gated=False)])
        )
    }
    assert excused["false_person_auto_link"].status is GateStatus.PASS


def test_overall_status_is_preliminary_with_draft_annotations_and_failed_with_hard_gate() -> None:
    policy, _ = load_policy(DEFAULT_POLICY_PATH)
    clean = RealWorldSafetyGateEvaluator(policy).evaluate(_inputs([]))
    status, code, reasons = overall_status(
        clean, verified_articles=0, draft_articles_evaluated=45, min_verified_articles=100
    )
    assert status is OverallStatus.PRELIMINARY and any("PRELIMINARY" in r for r in reasons)
    assert code == EXIT_OK

    broken = RealWorldSafetyGateEvaluator(policy).evaluate(
        _inputs([_failure(DangerousKind.FALSE_PERSON_LINK)])
    )
    status, code, _ = overall_status(
        broken, verified_articles=200, draft_articles_evaluated=0, min_verified_articles=100
    )
    assert (status, code) == (OverallStatus.FAILED_GATES, EXIT_GATES_FAILED)


def _report(failures: list[Failure]) -> RealWorldValidationReport:
    policy, policy_hash = load_policy(DEFAULT_POLICY_PATH)
    gates = RealWorldSafetyGateEvaluator(policy).evaluate(
        _inputs(
            failures,
            persecution=PersecutionSection(
                status=SectionStatus.RUN, political={"precision": 0.5, "recall": 0.9}
            ),
            entity_resolution=EntityResolutionSection(
                status=SectionStatus.RUN,
                namesake=NamesakeSection(status=SectionStatus.NOT_RUN, not_run_reason="none"),
            ),
        )
    )
    status, code, reasons = overall_status(
        gates, verified_articles=0, draft_articles_evaluated=1, min_verified_articles=100
    )
    empty = datetime(2026, 9, 15, tzinfo=UTC)
    return RealWorldValidationReport(
        evaluation_version="real-world-v1",
        overall_status=status,
        exit_code=code,
        status_reasons=reasons,
        provenance=Provenance(
            git_commit="abc",
            evaluation_version="real-world-v1",
            dataset_version="t",
            corpus_manifest_hash="m",
            golden_dataset_hash="g",
            rf_snapshot_id="rf",
            rf_snapshot_hash="h",
            embedding_model_id=None,
            extractor_version="e",
            classifier_version="c",
            matcher_version="m",
            resolver_version="r",
            component_versions={"extractor": "e"},
            policy_version=policy.policy_version,
            policy_hash=policy_hash,
            thresholds={},
            split="dev",
            verified_only=False,
            scenarios=[],
            timestamp=empty,
        ),
        dataset=DatasetSummary(
            corpus_articles=1,
            corpus_by_source={},
            corpus_by_period={},
            corpus_period=None,
            corpus_published_range=None,
            evaluation_sample=0,
            source_status={},
            golden_articles=1,
            golden_draft=1,
            golden_verified=0,
            golden_persons=2,
            golden_by_split={},
            evaluated_articles=1,
            evaluated_persons=2,
            namesake_cases=0,
            retrieval_queries=0,
            research_queries=0,
        ),
        extraction=ExtractionSection(status=SectionStatus.NOT_RUN),
        entity_resolution=EntityResolutionSection(status=SectionStatus.RUN),
        persecution=PersecutionSection(status=SectionStatus.RUN),
        rosfinmonitoring=RosfinSection(status=SectionStatus.NOT_RUN),
        candidate_query=CandidateSection(status=SectionStatus.NOT_RUN),
        retrieval=RetrievalSection(status=SectionStatus.NOT_RUN, not_run_reason="no model"),
        research=ResearchSection(status=SectionStatus.NOT_RUN),
        monitoring=MonitoringSection(status=SectionStatus.NOT_RUN),
        performance=PerformanceSection(status=SectionStatus.NOT_RUN),
        safety_gates=gates,
        failures=failures,
        failure_summary={},
        known_limitations=["DRAFT only"],
        recommended_next_work=recommended_next_work(gates, failures),
    )


def test_report_generation_has_every_section_and_ranks_hard_gates_first(tmp_path: Path) -> None:
    report = _report([_failure(DangerousKind.FALSE_PERSON_LINK)])
    markdown = render_markdown(report)
    for section in (
        "Executive Summary",
        "Dataset",
        "Pipeline",
        "Metrics",
        "Safety Gates",
        "Extraction",
        "Entity Resolution",
        "Persecution",
        "Rosfinmonitoring",
        "Candidate Query",
        "Semantic Retrieval",
        "Research Reports",
        "Monitoring E2E",
        "Failure Injection",
        "Performance",
        "Manual Review Workload",
        "Known Limitations",
        "Critical Failures",
        "Recommended Next Work",
    ):
        assert f"## {section}" in markdown
    assert "FAILED_GATES" in markdown
    assert "NOT_RUN" in markdown
    work = report.recommended_next_work
    assert work[0].metric == "false_person_auto_link"
    assert work[1].metric == "gated_dangerous_failures"
    assert work[2].metric == "political_precision"
    assert work == recommended_next_work(report.safety_gates, report.failures)

    json_path, md_path = write_reports(report, tmp_path)
    assert RealWorldValidationReport.model_validate_json(json_path.read_text()) == report
    assert md_path.read_text() == markdown


# -- state comparison, request substitution ------------------------------------------------


def test_state_comparison_ignores_order_and_counts_duplicates() -> None:
    first = {"persons": ["a", "b"], "findings": ["f"]}
    second = {"persons": ["b", "a"], "findings": ["f", "f"]}
    diffs = compare_snapshots(first, second)
    assert [(d.table, d.only_in_first, d.only_in_second) for d in diffs] == [("findings", 0, 1)]
    assert duplicate_counts(second)["findings"] == 1
    assert compare_snapshots(first, second, ignore=frozenset({"findings"})) == []


def test_request_snapshot_placeholder_is_removed_without_a_snapshot() -> None:
    request = {
        "object_type": "person",
        "criteria": {
            "persecution_status": "political",
            "rosfinmonitoring_status": "not_matched",
            "snapshot_id": "$SNAPSHOT",
        },
    }
    assert _substitute(request, 7)["criteria"]["snapshot_id"] == 7
    # Without a snapshot the RF criterion is dropped, never executed as "absent".
    assert _substitute(request, None)["criteria"] == {"persecution_status": "political"}


def test_candidate_ids_are_read_from_stored_scored_candidates() -> None:
    """Stored ER decisions nest the id: {"candidate": {"person_id": ..}, "features", "score"}.

    Reading a top-level "person_id" found nothing and reported candidate recall@5 = 0.0
    on the real corpus although ER ranked the true person first.
    """
    from evaluation.real_world.db_state import stored_candidate_person_ids

    stored: list[dict[str, Any]] = [
        {"candidate": {"person_id": 7, "canonical_name": "A"}, "features": {}, "score": {}},
        {"candidate": {"person_id": 3}, "features": {}, "score": {}},
        {"features": {}},
    ]
    assert stored_candidate_person_ids(stored) == (7, 3)
    assert stored_candidate_person_ids(None) == ()


def test_persecution_claims_contradict_only_the_opposite_definite_status() -> None:
    from evaluation.real_world.component_evaluation import IdentityMap
    from evaluation.real_world.research_eval import ClaimJudge
    from evaluation.real_world.results import ClaimSupport
    from persecution.models import PersecutionClassificationStatus
    from research.reports.models import (
        ResearchClaim,
        ResearchClaimBasis,
        ResearchClaimType,
        ResearchReportItem,
    )

    dataset = golden()
    identity = IdentityMap(golden_of_person={1: {"gp-petrov"}, 2: {"gp-sidorova"}})
    judge = ClaimJudge(dataset, state(), identity)
    claim = ResearchClaim(
        claim_type=ResearchClaimType.PERSECUTION_CLASSIFICATION,
        basis=ResearchClaimBasis.SOURCE_DOCUMENTS,
        text="x",
    )

    def support(person_id: int, status: str) -> ClaimSupport:
        item = ResearchReportItem.model_validate(
            {"person_id": person_id, "canonical_name": "x", "persecution_status": status}
        )
        return judge.judge(item, claim)[0]

    assert support(1, "non_political") is ClaimSupport.CONTRADICTED  # annotated political
    assert support(2, "political") is ClaimSupport.CONTRADICTED  # annotated non_political
    assert support(2, "needs_review") is ClaimSupport.PARTIALLY_SUPPORTED
    assert support(1, "political") is ClaimSupport.PARTIALLY_SUPPORTED  # right, but no citation

    undecided = dataset.model_copy(
        update={
            "persons": [
                dataset.persons[0].model_copy(
                    update={
                        "persecution": dataset.persons[0].persecution.model_copy(  # type: ignore[union-attr]
                            update={"expected_status": PersecutionClassificationStatus.UNCERTAIN}
                        )
                    }
                ),
                dataset.persons[1],
            ]
        }
    )
    assert (
        ClaimJudge(undecided, state(), identity).judge(
            ResearchReportItem.model_validate(
                {"person_id": 1, "canonical_name": "x", "persecution_status": "non_political"}
            ),
            claim,
        )[0]
        is ClaimSupport.UNSUPPORTED
    )


def test_incomplete_semantic_index_is_not_run_not_a_quality_result() -> None:
    """Real run: E5 hit CUDA OutOfMemoryError, nothing was indexed, and dense
    Recall@5 = 0.0 was reported as a retrieval quality failure."""
    from evaluation.real_world.evaluator import semantic_index_problem

    assert semantic_index_problem({"semantic_documents": 700, "semantic_indexed": 0}) == (
        "semantic index incomplete: 0/700 entities indexed (see monitoring_run_items)"
    )
    assert semantic_index_problem({"semantic_documents": 0, "semantic_indexed": 0}) == (
        "semantic index is empty"
    )
    assert semantic_index_problem({"semantic_documents": 700, "semantic_indexed": 700}) is None


def test_report_false_not_matched_is_counted_once() -> None:
    """A contradicted RF claim is both an itemized failure and a research dangerous count;
    the gate summed both and reported 2 for one false statement."""
    policy, _ = load_policy(DEFAULT_POLICY_PATH)
    claim_failure = Failure(
        component=ErrorComponent.REPORT,
        severity=Severity.S0,
        kind="claim_contradicted",
        detail="rs-17: rosfinmonitoring_status: listed",
        dangerous_kind=DangerousKind.FALSE_RF_NOT_MATCHED,
    )
    research = ResearchSection(
        status=SectionStatus.RUN, dangerous={DangerousKind.FALSE_RF_NOT_MATCHED.value: 1}
    )
    gates = {
        g.name: g
        for g in RealWorldSafetyGateEvaluator(policy).evaluate(
            _inputs([claim_failure], research=research)
        )
    }
    assert gates["false_rf_not_matched"].value == 1


@pytest.mark.parametrize("kind", list(DangerousKind), ids=lambda k: k.value)
def test_every_gated_dangerous_failure_fails_a_hard_gate(kind: DangerousKind) -> None:
    """External review: false_actionable_candidate, false_political_classification,
    unsupported_event_claim had no hard gate; one such S0 left the hard gates green."""
    policy, _ = load_policy(DEFAULT_POLICY_PATH)
    evaluator = RealWorldSafetyGateEvaluator(policy)

    gated = evaluator.evaluate(_inputs([_failure(kind)]))
    status, _, _ = overall_status(
        gated, verified_articles=200, draft_articles_evaluated=0, min_verified_articles=100
    )
    assert any(g.kind is GateKind.HARD and g.status is GateStatus.FAIL for g in gated)
    assert status is OverallStatus.FAILED_GATES

    excused = {g.name: g for g in evaluator.evaluate(_inputs([_failure(kind, gated=False)]))}
    assert excused["gated_dangerous_failures"].status is GateStatus.PASS


def test_dangerous_failure_gate_is_enforced_even_if_the_policy_omits_it() -> None:
    policy, _ = load_policy(DEFAULT_POLICY_PATH)
    trimmed = policy.model_copy(
        update={
            "hard_gates": {
                k: v for k, v in policy.hard_gates.items() if k != "gated_dangerous_failures"
            }
        }
    )
    gates = {
        g.name: g
        for g in RealWorldSafetyGateEvaluator(trimmed).evaluate(
            _inputs([_failure(DangerousKind.UNSUPPORTED_EVENT_CLAIM)])
        )
    }
    assert gates["gated_dangerous_failures"].status is GateStatus.FAIL


@pytest.mark.parametrize(
    ("status", "snapshot_id", "summary"),
    [
        (None, None, "RF: Не найден в перечне Росфинмониторинга."),
        ("needs_review", 1, "Итог: не найден в перечне; сопоставление выполнено."),
        ("not_matched", None, "Не найден в перечне Росфинмониторинга."),
    ],
)
def test_absence_wording_without_a_confirmed_not_matched_is_unsupported(
    status: str | None, snapshot_id: int | None, summary: str
) -> None:
    """External review: absence was detected only at the start of the summary."""
    from evaluation.real_world.component_evaluation import IdentityMap
    from evaluation.real_world.research_eval import ClaimJudge
    from evaluation.real_world.results import ClaimSupport
    from research.reports.models import (
        ResearchClaim,
        ResearchClaimBasis,
        ResearchClaimType,
        ResearchReportItem,
    )

    judge = ClaimJudge(golden(), state(), IdentityMap())
    item = ResearchReportItem.model_validate(
        {
            "person_id": 99,  # outside the golden dataset: absence is still judged
            "canonical_name": "x",
            "rosfinmonitoring_status": status,
            "snapshot_id": snapshot_id,
            "rosfinmonitoring_summary": summary,
        }
    )
    claim = ResearchClaim(
        claim_type=ResearchClaimType.ROSFINMONITORING_STATUS,
        basis=ResearchClaimBasis.ROSFINMONITORING_SNAPSHOT,
        text=summary,
    )
    support, kind, _ = judge.judge(item, claim)
    assert (support, kind) == (ClaimSupport.UNSUPPORTED, DangerousKind.UNSUPPORTED_ABSENCE_CLAIM)


def test_same_as_is_transitive_for_identity_mapping() -> None:
    """External review: A same_as B and B same_as C left A and C unrelated, so one
    canonical person holding A and C was reported as a false link."""
    from evaluation.real_world.component_evaluation import same_as_components

    dataset = golden()
    persons = [
        dataset.persons[0].model_copy(update={"golden_person_id": "a", "same_as": ["b"]}),
        dataset.persons[0].model_copy(update={"golden_person_id": "b", "same_as": ["c"]}),
        dataset.persons[0].model_copy(update={"golden_person_id": "c"}),
        dataset.persons[1].model_copy(update={"golden_person_id": "d"}),
    ]
    components = same_as_components(persons)
    assert components["a"] == components["b"] == components["c"]
    assert components["d"] != components["a"]

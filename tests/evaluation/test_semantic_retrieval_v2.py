"""Semantic Retrieval v2: query/judgment schema, metrics with unjudged entities,
threshold calibration, candidate pooling and the downstream case split."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from evaluation.real_world.golden import GoldenSplit, load_golden_dataset
from evaluation.real_world.retrieval_eval import (
    RealRetrievalQuery,
    load_retrieval_queries,
    query_problems,
)
from evaluation.semantic_v2 import report, retrieval
from evaluation.semantic_v2.retrieval import Ranked
from semantic_retrieval.models import RetrievalEntityType

PERSON = RetrievalEntityType.PERSON


def query(
    query_id: str = "q1",
    *,
    judgments: dict[str, int] | None = None,
    split: GoldenSplit = GoldenSplit.DEV,
    expected_no_match: bool = False,
    semantic_only: bool = False,
    tags: list[str] | None = None,
) -> RealRetrievalQuery:
    return RealRetrievalQuery(
        query_id=query_id,
        text="запрос",
        entity_type=PERSON,
        split=split,
        judgments={"a": 2} if judgments is None and not expected_no_match else judgments or {},
        expected_no_match=expected_no_match,
        semantic_only=semantic_only,
        tags=["x"] if tags is None else tags,
    )


def ranked(*items: tuple[str, float]) -> list[Ranked]:
    return [Ranked(key=key, score=dense, dense=dense) for key, dense in items]


# --- schema ------------------------------------------------------------------------


def test_grades_outside_zero_to_two_are_rejected() -> None:
    with pytest.raises(ValidationError, match="grades must be 0, 1 or 2"):
        query(judgments={"a": 3})


def test_a_positive_query_needs_a_relevant_entity() -> None:
    with pytest.raises(ValidationError, match="no relevant entity"):
        query(judgments={"a": 0})


def test_a_negative_query_has_no_relevant_entity_and_is_not_semantic_only() -> None:
    assert query(expected_no_match=True, judgments={"a": 0}).relevant == {}
    with pytest.raises(ValidationError, match="expected_no_match"):
        query(expected_no_match=True, judgments={"a": 1})
    with pytest.raises(ValidationError, match="expected_no_match"):
        query(expected_no_match=True, semantic_only=True)


def test_query_problems_report_duplicates_missing_tags_and_other_split_relevant() -> None:
    dataset = load_golden_dataset()
    person = next(
        p.golden_person_id
        for p in dataset.persons
        if dataset.person_split(p.golden_person_id) is GoldenSplit.TEST
    )
    problems = query_problems(
        [
            query("q1", judgments={person: 2}),
            query("q1", judgments={"extra-person:X": 1}, tags=[]),
        ],
        dataset,
    )
    assert any("duplicate query_id" in p for p in problems)
    assert any("no tags" in p for p in problems)
    assert any(f"relevant {person} is in test split" in p for p in problems)


def test_committed_queries_are_valid_and_cover_the_required_classes() -> None:
    queries = load_retrieval_queries()
    assert query_problems(queries, load_golden_dataset()) == []
    positive_person = [q for q in queries if q.entity_type is PERSON and not q.expected_no_match]
    assert len(queries) >= 100
    assert len(positive_person) >= 60
    assert sum(q.entity_type is RetrievalEntityType.EVENT for q in queries) >= 15
    assert sum(q.expected_no_match for q in queries) >= 15
    assert sum(q.semantic_only for q in queries) >= 25
    assert sum("group" in q.tags for q in queries) >= 15


def test_the_test_split_is_never_run() -> None:
    queries = [
        query("d"),
        query("v", split=GoldenSplit.VALIDATION),
        query("t", split=GoldenSplit.TEST),
    ]
    assert [q.query_id for q in retrieval.runnable(queries)] == ["d", "v"]


# --- metrics: unjudged is not negative ----------------------------------------------


def test_an_unjudged_entity_gets_no_gain_and_lowers_coverage_not_relevance() -> None:
    ranking = ranked(("unjudged", 0.9), ("a", 0.8), ("b", 0.7))
    row = retrieval.query_metrics(ranking, {"a": 2, "b": 0}, reachable={"a", "b", "unjudged"})
    assert row is not None
    assert row["mrr"] == 0.5
    assert row["recall@5"] == 1.0
    assert row["coverage@5"] == pytest.approx(2 / 3)
    # Judged-only (condensed) ranking puts "a" first: the unjudged entity is not counted as wrong.
    assert row["ndcg@5_condensed"] == 1.0
    assert row["ndcg@5"] < 1.0


def test_a_query_whose_relevant_entities_are_not_indexed_has_no_ranking_metrics() -> None:
    assert retrieval.query_metrics(ranked(("x", 0.9)), {"a": 2}, reachable={"x"}) is None


def test_acceptance_keeps_unjudged_accepted_entities_out_of_precision() -> None:
    queries = [
        query("p", judgments={"a": 2, "b": 0}),
        query("n", expected_no_match=True, tags=["negative_hard"]),
    ]
    rankings = {
        "p": ranked(("a", 0.9), ("u", 0.85), ("b", 0.84), ("c", 0.5)),
        "n": ranked(("z", 0.86)),
    }
    judgments = retrieval.all_judgments(queries, {})
    row = retrieval.acceptance(
        queries, rankings, judgments, {PERSON: {"a", "b", "u", "c", "z"}}, 0.84
    )
    assert row["recall_micro"] == 1.0
    assert row["precision_judged"] == 0.5  # a accepted, b accepted; u is unjudged
    assert row["accepted_unjudged"] == 1
    assert row["rejection_rate"] == 0.0
    assert row["hard_negative_fp"] == 1
    stricter = retrieval.acceptance(queries, rankings, judgments, {PERSON: {"a", "b"}}, 0.87)
    assert stricter["rejection_rate"] == 1.0
    assert stricter["hard_negative_fp"] == 0


def test_pool_judgments_never_override_query_judgments() -> None:
    q = query(judgments={"a": 2})
    assert retrieval.all_judgments([q], {"q1": {"a": 0, "b": 1}}) == {"q1": {"a": 2, "b": 1}}


# --- threshold sweep and selection --------------------------------------------------


def test_threshold_selection_maximises_recall_within_the_baseline_constraints() -> None:
    baseline = {"precision_judged": 0.5, "rejection_rate": 0.5, "hard_negative_fp": 3}
    rows = [
        {
            "threshold": 0.40,
            "recall_micro": 0.9,
            "recall_macro": 0.9,
            "precision_judged": 0.4,
            "rejection_rate": 0.5,
            "hard_negative_fp": 1,
        },
        {
            "threshold": 0.45,
            "recall_micro": 0.8,
            "recall_macro": 0.8,
            "precision_judged": 0.6,
            "rejection_rate": 0.5,
            "hard_negative_fp": 2,
        },
        {
            "threshold": 0.50,
            "recall_micro": 0.8,
            "recall_macro": 0.8,
            "precision_judged": 0.7,
            "rejection_rate": 1.0,
            "hard_negative_fp": 0,
        },
        {
            "threshold": 0.55,
            "recall_micro": 0.7,
            "recall_macro": 0.7,
            "precision_judged": 0.9,
            "rejection_rate": 1.0,
            "hard_negative_fp": 0,
        },
    ]
    chosen = retrieval.select_threshold(rows, baseline)
    assert chosen is not None
    assert chosen["threshold"] == 0.50  # 0.40 breaks precision; tie at 0.8 -> the higher threshold
    assert retrieval.select_threshold(rows[:1], baseline) is None


def test_sweep_is_monotonic_in_the_number_of_accepted_entities() -> None:
    queries = [query("p", judgments={"a": 2})]
    rankings = {"p": ranked(("a", 0.9), ("u", 0.6))}
    rows = retrieval.sweep(
        queries, rankings, {"p": {"a": 2}}, {PERSON: {"a", "u"}}, [0.5, 0.7, 0.95]
    )
    assert [r["accepted_unjudged"] for r in rows] == [1, 0, 0]
    assert [r["recall_micro"] for r in rows] == [1.0, 1.0, 0.0]
    assert retrieval.thresholds_grid(0.3, 0.32, 0.01) == [0.3, 0.31, 0.32]


# --- candidate pooling --------------------------------------------------------------


def test_pooling_dedups_keys_and_skips_judged_ones() -> None:
    q = query("p", judgments={"a": 2})
    deep = [(f"filler{i}", 0.5) for i in range(25)]
    runs = {
        "e5_dense": {"p": ranked(("a", 0.9), ("x", 0.85), *deep[:18], ("t", 0.801), ("d", 0.7))},
        "bge_m3_dense": {"p": ranked(("x", 0.6), ("a", 0.55), *deep)},
    }
    pool = retrieval.build_pool(
        [q], runs, {"p": {"a": 2}}, {"e5": [0.80]}, {"e5": 0.005, "bge_m3": 0.01}
    )
    reasons = pool["p"]
    assert "a" not in reasons  # judged: not pooled again
    assert reasons["x"] == ["top20:e5_dense", "top20:bge_m3_dense"]  # one key, both reasons
    assert reasons["t"] == ["threshold_sensitive:e5@21~0.8", "disagreement:e5@21,absent_in_bge_m3"]
    assert reasons["d"] == ["disagreement:e5@22,absent_in_bge_m3"]


def test_merged_marks_judge_unmarked_pool_keys_zero_and_separate_cross_split() -> None:
    queries = [query("p", judgments={"a": 2})]
    pending = {"p": {"x": ["top20"], "y": ["top20"], "z": ["top20"]}}
    marks = {"p": {"q": "p", "rel": {"y": 1, "z": 2}, "retire": "partial match"}}
    splits: dict[str, set[GoldenSplit | None]] = {
        "y": {GoldenSplit.DEV},
        "z": {GoldenSplit.TEST, None},
    }
    payload = retrieval.merge_marks(queries, pending, marks, splits, previous={})
    assert payload["status"] == "DRAFT"
    assert payload["judgments"] == {"p": {"x": 0, "y": 1}}
    assert payload["cross_split"] == {"p": {"z": 2}}
    assert payload["retired"] == {"p": "partial match"}
    with pytest.raises(SystemExit, match="no marks"):
        retrieval.merge_marks(queries, pending, {}, splits, previous={})


def test_an_entity_only_outside_golden_articles_is_not_cross_split() -> None:
    q = query()
    assert not retrieval.is_cross_split(q, {None})
    assert not retrieval.is_cross_split(q, {GoldenSplit.DEV, GoldenSplit.TEST})
    assert retrieval.is_cross_split(q, {GoldenSplit.VALIDATION})


# --- downstream ---------------------------------------------------------------------


def test_downstream_cases_split_queries_by_which_model_answers_them() -> None:
    queries = [
        query("both", judgments={"a": 2}),
        query("e5", judgments={"a": 2, "b": 1}),
        query("neg", expected_no_match=True),
    ]
    judgments = retrieval.all_judgments(queries, {"both": {"n": 0}})
    downstream: dict[str, Any] = {
        "configurations": {
            "e5@0.8": {"dev": {"returned": {"both": ["a", "n"], "e5": ["b", "a"], "neg": ["z"]}}},
            "bge_m3@0.47": {"dev": {"returned": {"both": ["a"], "e5": ["a"], "neg": []}}},
        }
    }
    result = report.downstream_cases(queries, judgments, downstream, "e5@0.8", "bge_m3@0.47")
    assert result["counts"] == {
        "e5_correct_bge_wrong": 1,
        "bge_correct_e5_wrong": 2,
        "both_wrong": 0,
        "both_correct": 0,
    }
    e5_case = result["cases"]["e5_correct_bge_wrong"][0]
    assert e5_case["query_id"] == "e5"
    assert e5_case["bge_m3"]["missed"] == ["b"]
    assert e5_case["e5"]["relevant_ranks"] == {"a": 2, "b": 1}
    # "both": E5 returned the judged non-relevant "n"; on recall alone both are correct.
    assert result["recall_only"]["queries"]["both_correct"] == ["both"]

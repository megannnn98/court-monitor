"""Relevance acceptance evaluation: threshold sweep over positive and negative cases."""

from __future__ import annotations

import pytest
from semantic_fakes import StaticRetriever

from semantic_retrieval.evaluation import (
    CorpusIds,
    EntityRetrievalCase,
    evaluate_acceptance,
    format_acceptance,
)
from semantic_retrieval.models import RetrievalBackend, RetrievalEntityType

PERSON = RetrievalEntityType.PERSON
IDS = CorpusIds(persons={"a": 1, "b": 2, "c": 3, "d": 4}, events={})


def _case(query_id: str, judgments: dict[str, int], **flags: bool) -> EntityRetrievalCase:
    return EntityRetrievalCase(
        query_id=query_id, query_text=query_id, entity_type=PERSON, judgments=judgments, **flags
    )


def test_negative_case_must_have_no_relevant_entity_and_positive_needs_one() -> None:
    assert _case("neg", {}, negative=True).negative is True
    with pytest.raises(ValueError, match="negative"):
        _case("neg", {"a": 1}, negative=True)
    with pytest.raises(ValueError, match="no relevant entity"):
        _case("pos", {})
    with pytest.raises(ValueError, match="negative"):
        _case("both", {}, negative=True, semantic_only=True)


class _PerQueryRetriever:
    """Different dense similarities per query text."""

    def __init__(self, by_query: dict[str, dict[int, float]]) -> None:
        self._by_query = by_query

    def retrieve(self, query):  # type: ignore[no-untyped-def]
        scores = self._by_query[query.text]
        ordered = sorted(scores, key=lambda entity_id: -scores[entity_id])
        return StaticRetriever(RetrievalBackend.HYBRID, ordered, dense_scores=scores).retrieve(
            query
        )


def test_threshold_sweep_separates_recall_precision_and_negative_rejection() -> None:
    cases = [
        _case("pos", {"a": 2, "b": 1}),
        _case("sem", {"c": 2}, semantic_only=True),
        _case("neg", {}, negative=True),
    ]
    retriever = _PerQueryRetriever(
        {
            "pos": {1: 0.85, 2: 0.78, 4: 0.81},
            "sem": {3: 0.79, 4: 0.70},
            "neg": {1: 0.77, 2: 0.76},
        }
    )

    rows = {
        row.threshold: row
        for row in evaluate_acceptance(
            retriever=retriever,
            cases=cases,
            ids=IDS,
            thresholds=[0.75, 0.80],
            default_threshold=0.80,
        ).rows
    }

    low, high = rows[0.75], rows[0.80]
    assert low.relevant_recall == pytest.approx(1.0)
    assert (low.negative_rejection_rate, low.negative_false_positives) == (0.0, 2)
    assert low.precision == pytest.approx(3 / 4)
    assert high.relevant_recall == pytest.approx(1 / 3)  # only a (0.85) of a, b, c
    assert high.grade2_recall == pytest.approx(1 / 2)
    assert high.semantic_only_recall == 0.0
    assert high.precision == pytest.approx(1 / 2)  # a relevant, d (0.81) not
    assert (high.negative_rejection_rate, high.negative_false_positives) == (1.0, 0)
    assert high.positive_cases_with_accepted_relevant == 1
    assert high.is_default and not low.is_default


def test_default_grid_follows_observed_similarities_and_includes_default() -> None:
    cases = [_case("pos", {"a": 2}), _case("neg", {}, negative=True)]
    retriever = _PerQueryRetriever({"pos": {1: 0.861}, "neg": {2: 0.742}})

    evaluation = evaluate_acceptance(
        retriever=retriever, cases=cases, ids=IDS, thresholds=None, default_threshold=0.80
    )

    thresholds = [row.threshold for row in evaluation.rows]
    assert thresholds[0] == pytest.approx(0.74)
    assert thresholds[-1] == pytest.approx(0.87)
    assert 0.80 in thresholds
    assert thresholds == sorted(thresholds)
    assert evaluation.observed_min == pytest.approx(0.742)
    assert evaluation.observed_max == pytest.approx(0.861)


def test_acceptance_table_marks_the_default_threshold() -> None:
    cases = [_case("pos", {"a": 2}), _case("neg", {}, negative=True)]
    retriever = _PerQueryRetriever({"pos": {1: 0.9}, "neg": {2: 0.7}})
    evaluation = evaluate_acceptance(
        retriever=retriever, cases=cases, ids=IDS, thresholds=[0.8], default_threshold=0.8
    )

    table = format_acceptance(evaluation)

    assert table.splitlines()[0].startswith("| dense min score | relevant recall |")
    assert "| **0.800** (default) |" in table

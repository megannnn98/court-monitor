"""Graded ranking metrics for entity retrieval evaluation."""

from __future__ import annotations

import math

import pytest

from semantic_retrieval.metrics import (
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

JUDGMENTS = {10: 2, 20: 1, 30: 1}


def test_reciprocal_rank_uses_first_relevant_entity() -> None:
    assert reciprocal_rank([99, 20, 10], JUDGMENTS) == pytest.approx(1 / 2)
    assert reciprocal_rank([99, 98], JUDGMENTS) == 0.0


def test_zero_relevance_judgment_is_not_relevant() -> None:
    assert reciprocal_rank([5, 10], {5: 0, 10: 2}) == pytest.approx(1 / 2)
    assert recall_at_k([5], {5: 0, 10: 2}, k=1) == 0.0


def test_recall_and_precision_at_k() -> None:
    retrieved = [10, 99, 30, 20]

    assert recall_at_k(retrieved, JUDGMENTS, k=3) == pytest.approx(2 / 3)
    assert precision_at_k(retrieved, JUDGMENTS, k=3) == pytest.approx(2 / 3)
    assert recall_at_k(retrieved, JUDGMENTS, k=10) == pytest.approx(1.0)
    # Precision divides by k even when fewer entities were retrieved.
    assert precision_at_k([10], JUDGMENTS, k=5) == pytest.approx(1 / 5)


def test_ndcg_uses_graded_gains_and_ideal_ordering() -> None:
    retrieved = [20, 10, 99]
    dcg = (2**1 - 1) / math.log2(2) + (2**2 - 1) / math.log2(3)
    ideal = (2**2 - 1) / math.log2(2) + (2**1 - 1) / math.log2(3) + (2**1 - 1) / math.log2(4)

    assert ndcg_at_k(retrieved, JUDGMENTS, k=3) == pytest.approx(dcg / ideal)
    assert ndcg_at_k([10, 20, 30], JUDGMENTS, k=3) == pytest.approx(1.0)


def test_no_relevant_judgments_scores_zero() -> None:
    assert ndcg_at_k([1], {}, k=5) == 0.0
    assert recall_at_k([1], {}, k=5) == 0.0


def test_duplicates_in_ranking_are_counted_once() -> None:
    assert precision_at_k([10, 10, 20], JUDGMENTS, k=3) == pytest.approx(2 / 3)
    assert ndcg_at_k([10, 10], {10: 2}, k=2) == pytest.approx(1.0)

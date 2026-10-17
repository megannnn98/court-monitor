"""Ranking metrics over binary relevance: Recall@k, MRR, NDCG@k."""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence


def recall_at(ranked: Sequence[object], relevant: Collection[object], k: int) -> float:
    """The share of the relevant found in the first `k`."""
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & set(relevant)) / len(relevant)


def reciprocal_rank(ranked: Sequence[object], relevant: Collection[object]) -> float:
    for position, item in enumerate(ranked, 1):
        if item in relevant:
            return 1.0 / position
    return 0.0


def ndcg_at(ranked: Sequence[object], relevant: Collection[object], k: int) -> float:
    gains = [
        1.0 / math.log2(position + 1)
        for position, item in enumerate(ranked[:k], 1)
        if item in relevant
    ]
    ideal = sum(1.0 / math.log2(position + 1) for position in range(1, min(len(relevant), k) + 1))
    return sum(gains) / ideal if ideal else 0.0


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0

"""Ranking metrics with graded relevance (0 = not relevant, 1 = relevant, 2 = highly)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


def _unique(retrieved: Sequence[int]) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for entity_id in retrieved:
        if entity_id not in seen:
            seen.add(entity_id)
            ordered.append(entity_id)
    return ordered


def _relevant(judgments: Mapping[int, int]) -> set[int]:
    return {entity_id for entity_id, grade in judgments.items() if grade > 0}


def reciprocal_rank(retrieved: Sequence[int], judgments: Mapping[int, int]) -> float:
    relevant = _relevant(judgments)
    for rank, entity_id in enumerate(_unique(retrieved), start=1):
        if entity_id in relevant:
            return 1.0 / rank
    return 0.0


def recall_at_k(retrieved: Sequence[int], judgments: Mapping[int, int], *, k: int) -> float:
    relevant = _relevant(judgments)
    if not relevant:
        return 0.0
    return len(relevant.intersection(_unique(retrieved)[:k])) / len(relevant)


def precision_at_k(retrieved: Sequence[int], judgments: Mapping[int, int], *, k: int) -> float:
    if k <= 0:
        raise ValueError("k must be greater than 0")
    return len(_relevant(judgments).intersection(_unique(retrieved)[:k])) / k


def _dcg(grades: Sequence[int]) -> float:
    return sum(
        (2.0**grade - 1.0) / math.log2(position + 1) for position, grade in enumerate(grades, 1)
    )


def ndcg_at_k(retrieved: Sequence[int], judgments: Mapping[int, int], *, k: int) -> float:
    ideal = _dcg(sorted((grade for grade in judgments.values() if grade > 0), reverse=True)[:k])
    if ideal == 0.0:
        return 0.0
    actual = _dcg([judgments.get(entity_id, 0) for entity_id in _unique(retrieved)[:k]])
    return actual / ideal

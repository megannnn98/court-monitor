"""Metric primitives for real-world validation.

Precision/recall/F1 reuse the final evaluation's `Counts` (None for a zero
denominator); ranking metrics reuse `semantic_retrieval.metrics`. Nothing here
touches the database.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from evaluation.final.models import Counts


def rate(numerator: int, denominator: int) -> float | None:
    """None when nothing was measured: never a silent 0 or 1."""
    return None if denominator == 0 else round(numerator / denominator, 4)


def per_100(count: int, articles: int) -> float | None:
    return None if articles == 0 else round(100 * count / articles, 2)


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    label: str = ""

    def overlap(self, other: Span) -> int:
        return max(0, min(self.end, other.end) - max(self.start, other.start))


def match_spans(
    gold: Sequence[Span], predicted: Sequence[Span], *, same_label: bool = True
) -> list[tuple[int, int]]:
    """One-to-one matching of overlapping spans, largest overlap first.

    Returns (gold index, predicted index) pairs; ties break by position so the
    matching is deterministic.
    """
    pairs = sorted(
        (
            (-g.overlap(p), gi, pi)
            for gi, g in enumerate(gold)
            for pi, p in enumerate(predicted)
            if g.overlap(p) > 0 and (not same_label or g.label == p.label)
        )
    )
    used_gold: set[int] = set()
    used_predicted: set[int] = set()
    matches: list[tuple[int, int]] = []
    for _, gi, pi in pairs:
        if gi in used_gold or pi in used_predicted:
            continue
        used_gold.add(gi)
        used_predicted.add(pi)
        matches.append((gi, pi))
    return sorted(matches)


def span_counts(
    gold: Sequence[Span], predicted: Sequence[Span], *, same_label: bool = True
) -> Counts:
    matched = len(match_spans(gold, predicted, same_label=same_label))
    return Counts(tp=matched, fp=len(predicted) - matched, fn=len(gold) - matched)


def confusion_matrix(
    pairs: Iterable[tuple[str, str]], labels: Sequence[str]
) -> dict[str, dict[str, int]]:
    """expected -> actual -> count, with every label present (zeros included)."""
    pair_list = list(pairs)
    actual_labels = list(labels) + sorted({a for _, a in pair_list} - set(labels))
    matrix = {expected: dict.fromkeys(actual_labels, 0) for expected in labels}
    for expected, actual in pair_list:
        row = matrix.setdefault(expected, dict.fromkeys(actual_labels, 0))
        row[actual] = row.get(actual, 0) + 1
    return matrix


def binary_counts(pairs: Iterable[tuple[bool, bool]]) -> Counts:
    counts = Counts()
    for expected, actual in pairs:
        counts.add(expected=expected, actual=actual)
    return counts


def prf(counts: Counts) -> dict[str, float | int | None]:
    return counts.summary()


def mean(values: Sequence[float]) -> float | None:
    return None if not values else round(sum(values) / len(values), 4)


def majority[L: Hashable](values: Iterable[L]) -> L | None:
    """Most frequent value; ties resolve to the smallest by repr (deterministic)."""
    counts: dict[L, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return min(counts, key=lambda value: (-counts[value], repr(value)))


def recall_at(ranked: Sequence[object], truth: object, ks: Iterable[int]) -> Mapping[int, bool]:
    return {k: truth in list(ranked)[:k] for k in ks}

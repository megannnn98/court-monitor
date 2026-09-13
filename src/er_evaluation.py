"""Evaluation framework for entity resolution."""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

from pydantic import BaseModel, Field


class EntityResolutionCase(BaseModel):
    """Single entity resolution test case: a set of mentions that should resolve to the same person."""

    person_id: str
    mentions: list[MentionRef] = Field(default_factory=list)


class MentionRef(BaseModel):
    """Reference to a mention in an article."""

    article_id: int
    surface_text: str


class EntityResolutionDataset(BaseModel):
    """Dataset for entity resolution evaluation."""

    cases: list[EntityResolutionCase] = Field(default_factory=list)


class EntityResolutionReport(BaseModel):
    """Report of entity resolution evaluation."""

    total_pairs_expected: int = 0
    total_pairs_actual: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    pairwise_precision: float = 0.0
    pairwise_recall: float = 0.0
    pairwise_f1: float = 0.0


def load_er_dataset(path: Path) -> EntityResolutionDataset:
    """Load entity resolution evaluation dataset."""
    return EntityResolutionDataset.model_validate_json(path.read_text(encoding="utf-8"))


def compute_pairwise_f1(
    expected_clusters: list[set[tuple[int, str]]],
    actual_clusters: list[set[tuple[int, str]]],
) -> EntityResolutionReport:
    """Compute pairwise F1 for entity resolution.

    Args:
        expected_clusters: List of sets of (article_id, surface_text) tuples,
                          each set represents mentions that should be the same person.
        actual_clusters: List of sets of (article_id, surface_text) tuples,
                        each set represents mentions resolved to the same person.

    Returns:
        EntityResolutionReport with pairwise precision, recall, F1.
    """
    expected_pairs: set[tuple[tuple[int, str], tuple[int, str]]] = set()
    for cluster in expected_clusters:
        for m1, m2 in combinations(sorted(cluster), 2):
            expected_pairs.add((m1, m2))

    actual_pairs: set[tuple[tuple[int, str], tuple[int, str]]] = set()
    for cluster in actual_clusters:
        for m1, m2 in combinations(sorted(cluster), 2):
            actual_pairs.add((m1, m2))

    tp = len(expected_pairs & actual_pairs)
    fp = len(actual_pairs - expected_pairs)
    fn = len(expected_pairs - actual_pairs)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return EntityResolutionReport(
        total_pairs_expected=len(expected_pairs),
        total_pairs_actual=len(actual_pairs),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        pairwise_precision=precision,
        pairwise_recall=recall,
        pairwise_f1=f1,
    )

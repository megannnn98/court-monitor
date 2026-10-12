"""Evaluation framework for Rosfinmonitoring matching."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class RosfinMatchCase(BaseModel):
    """Single Rosfinmonitoring matching test case."""

    person_id: int
    expected_match: bool
    expected_entry_id: int | None = None
    description: str = ""


class RosfinMatchDataset(BaseModel):
    """Dataset for Rosfinmonitoring matching evaluation."""

    snapshot_id: int
    cases: list[RosfinMatchCase] = Field(default_factory=list)


class RosfinMatchReport(BaseModel):
    """Report of Rosfinmonitoring matching evaluation."""

    total_cases: int = 0
    true_positives: int = 0
    false_positives: int = 0
    true_negatives: int = 0
    false_negatives: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0


def load_rosfin_match_dataset(path: Path) -> RosfinMatchDataset:
    """Load Rosfinmonitoring matching evaluation dataset."""
    return RosfinMatchDataset.model_validate_json(path.read_text(encoding="utf-8"))


def compute_rosfin_match_metrics(
    expected: list[bool],
    actual: list[bool],
) -> RosfinMatchReport:
    """Compute precision/recall/F1 for Rosfinmonitoring matching.

    Args:
        expected: List of expected match outcomes (True = should match, False = should not).
        actual: List of actual match outcomes from the matcher.

    Returns:
        RosfinMatchReport with precision, recall, F1.
    """
    if len(expected) != len(actual):
        raise ValueError("Expected and actual lists must have the same length")

    tp = sum(1 for e, a in zip(expected, actual) if e and a)
    fp = sum(1 for e, a in zip(expected, actual) if not e and a)
    tn = sum(1 for e, a in zip(expected, actual) if not e and not a)
    fn = sum(1 for e, a in zip(expected, actual) if e and not a)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return RosfinMatchReport(
        total_cases=len(expected),
        true_positives=tp,
        false_positives=fp,
        true_negatives=tn,
        false_negatives=fn,
        precision=precision,
        recall=recall,
        f1=f1,
    )

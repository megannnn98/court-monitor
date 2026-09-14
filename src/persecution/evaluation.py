"""Evaluation framework for political persecution classification."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class PersecutionCase(BaseModel):
    """Single political persecution classification test case."""

    person_id: int
    expected_status: str  # "political", "non_political", "uncertain"
    expected_confidence_min: float = 0.0
    expected_confidence_max: float = 1.0
    description: str = ""


class PersecutionDataset(BaseModel):
    """Dataset for political persecution classification evaluation."""

    cases: list[PersecutionCase] = Field(default_factory=list)


class PersecutionReport(BaseModel):
    """Report of political persecution classification evaluation."""

    total_cases: int = 0
    correct_classifications: int = 0
    accuracy: float = 0.0
    political_precision: float = 0.0
    political_recall: float = 0.0
    political_f1: float = 0.0


def load_persecution_dataset(path: Path) -> PersecutionDataset:
    """Load political persecution classification evaluation dataset."""
    return PersecutionDataset.model_validate_json(path.read_text(encoding="utf-8"))


def compute_persecution_metrics(
    expected: list[str],
    actual: list[str],
) -> PersecutionReport:
    """Compute accuracy and political class precision/recall/F1.

    Args:
        expected: List of expected statuses.
        actual: List of actual statuses from the classifier.

    Returns:
        PersecutionReport with accuracy and political class metrics.
    """
    if len(expected) != len(actual):
        raise ValueError("Expected and actual lists must have the same length")

    total = len(expected)
    correct = sum(1 for e, a in zip(expected, actual) if e == a)
    accuracy = correct / total if total > 0 else 0.0

    # Compute metrics for "political" class
    tp = sum(1 for e, a in zip(expected, actual) if e == "political" and a == "political")
    fp = sum(1 for e, a in zip(expected, actual) if e != "political" and a == "political")
    fn = sum(1 for e, a in zip(expected, actual) if e == "political" and a != "political")

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return PersecutionReport(
        total_cases=total,
        correct_classifications=correct,
        accuracy=accuracy,
        political_precision=precision,
        political_recall=recall,
        political_f1=f1,
    )

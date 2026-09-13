"""Tests for entity resolution evaluation."""

from pathlib import Path

from er_evaluation import (
    EntityResolutionCase,
    EntityResolutionDataset,
    MentionRef,
    compute_pairwise_f1,
    load_er_dataset,
)


def test_perfect_clustering() -> None:
    """Test perfect clustering: expected == actual."""
    expected = [
        {(1, "А"), (2, "А")},
        {(1, "Б"), (2, "Б")},
    ]
    actual = [
        {(1, "А"), (2, "А")},
        {(1, "Б"), (2, "Б")},
    ]

    report = compute_pairwise_f1(expected, actual)

    assert report.pairwise_precision == 1.0
    assert report.pairwise_recall == 1.0
    assert report.pairwise_f1 == 1.0
    assert report.true_positives == 2
    assert report.false_positives == 0
    assert report.false_negatives == 0


def test_completely_wrong_clustering() -> None:
    """Test completely wrong clustering: all mentions in one cluster."""
    expected = [
        {(1, "А"), (2, "А")},
        {(1, "Б"), (2, "Б")},
    ]
    actual = [
        {(1, "А"), (2, "А"), (1, "Б"), (2, "Б")},
    ]

    report = compute_pairwise_f1(expected, actual)

    assert report.true_positives == 2
    assert report.false_positives == 4
    assert report.false_negatives == 0
    assert report.pairwise_precision == 2 / 6
    assert report.pairwise_recall == 1.0


def test_over_split_clustering() -> None:
    """Test over-split: each mention in its own cluster."""
    expected = [
        {(1, "А"), (2, "А")},
    ]
    actual = [
        {(1, "А")},
        {(2, "А")},
    ]

    report = compute_pairwise_f1(expected, actual)

    assert report.true_positives == 0
    assert report.false_positives == 0
    assert report.false_negatives == 1
    assert report.pairwise_precision == 0.0
    assert report.pairwise_recall == 0.0
    assert report.pairwise_f1 == 0.0


def test_empty_clustering() -> None:
    """Test empty clustering."""
    expected: list[set[tuple[int, str]]] = []
    actual: list[set[tuple[int, str]]] = []

    report = compute_pairwise_f1(expected, actual)

    assert report.pairwise_precision == 0.0
    assert report.pairwise_recall == 0.0
    assert report.pairwise_f1 == 0.0


def test_load_er_dataset() -> None:
    """Test loading ER dataset from JSON."""
    path = Path("tests/fixtures/er_golden_dataset.json")
    if not path.exists():
        return

    dataset = load_er_dataset(path)

    assert isinstance(dataset, EntityResolutionDataset)
    assert len(dataset.cases) > 0
    assert all(isinstance(case, EntityResolutionCase) for case in dataset.cases)
    assert all(isinstance(m, MentionRef) for case in dataset.cases for m in case.mentions)

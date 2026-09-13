"""Tests for political persecution classification evaluation."""

import pytest

from persecution_evaluation import (
    compute_persecution_metrics,
)


def test_perfect_classification() -> None:
    """Test perfect classification."""
    expected = ["political", "political", "non_political", "non_political", "uncertain"]
    actual = ["political", "political", "non_political", "non_political", "uncertain"]

    report = compute_persecution_metrics(expected, actual)

    assert report.accuracy == 1.0
    assert report.political_precision == 1.0
    assert report.political_recall == 1.0
    assert report.political_f1 == 1.0
    assert report.correct_classifications == 5


def test_all_wrong() -> None:
    """Test all wrong classifications."""
    expected = ["political", "political", "non_political"]
    actual = ["non_political", "non_political", "political"]

    report = compute_persecution_metrics(expected, actual)

    assert report.accuracy == 0.0
    assert report.correct_classifications == 0
    assert report.political_precision == 0.0
    assert report.political_recall == 0.0
    assert report.political_f1 == 0.0


def test_political_class_metrics() -> None:
    """Test political class precision/recall/f1."""
    expected = ["political", "political", "political", "non_political", "non_political"]
    actual = ["political", "political", "non_political", "non_political", "political"]

    report = compute_persecution_metrics(expected, actual)

    # 2 TP, 1 FP, 1 FN for political class
    assert report.political_precision == 2 / 3  # 2 TP / (2 TP + 1 FP)
    assert report.political_recall == 2 / 3  # 2 TP / (2 TP + 1 FN)
    assert report.political_f1 == 2 / 3  # harmonic mean
    assert report.accuracy == 3 / 5  # 3 correct out of 5


def test_no_political_cases() -> None:
    """Test when there are no political cases."""
    expected = ["non_political", "non_political", "uncertain"]
    actual = ["non_political", "non_political", "uncertain"]

    report = compute_persecution_metrics(expected, actual)

    assert report.accuracy == 1.0
    assert report.political_precision == 0.0  # no political predictions
    assert report.political_recall == 0.0  # no political cases
    assert report.political_f1 == 0.0


def test_length_mismatch_raises_error() -> None:
    """Test that mismatched lengths raise ValueError."""
    expected = ["political", "non_political"]
    actual = ["political", "non_political", "uncertain"]

    with pytest.raises(ValueError, match="same length"):
        compute_persecution_metrics(expected, actual)


def test_empty_lists() -> None:
    """Test empty lists."""
    expected: list[str] = []
    actual: list[str] = []

    report = compute_persecution_metrics(expected, actual)

    assert report.total_cases == 0
    assert report.accuracy == 0.0
    assert report.political_precision == 0.0
    assert report.political_recall == 0.0
    assert report.political_f1 == 0.0

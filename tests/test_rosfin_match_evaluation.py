"""Tests for Rosfinmonitoring matching evaluation."""

import pytest

from rosfin_match_evaluation import (
    compute_rosfin_match_metrics,
)


def test_perfect_matching() -> None:
    """Test perfect matching: all predictions correct."""
    expected = [True, True, False, False, True]
    actual = [True, True, False, False, True]

    report = compute_rosfin_match_metrics(expected, actual)

    assert report.precision == 1.0
    assert report.recall == 1.0
    assert report.f1 == 1.0
    assert report.true_positives == 3
    assert report.true_negatives == 2
    assert report.false_positives == 0
    assert report.false_negatives == 0


def test_all_false_positives() -> None:
    """Test all false positives."""
    expected = [False, False, False]
    actual = [True, True, True]

    report = compute_rosfin_match_metrics(expected, actual)

    assert report.precision == 0.0
    assert report.recall == 0.0
    assert report.f1 == 0.0
    assert report.true_positives == 0
    assert report.false_positives == 3
    assert report.true_negatives == 0
    assert report.false_negatives == 0


def test_all_false_negatives() -> None:
    """Test all false negatives."""
    expected = [True, True, True]
    actual = [False, False, False]

    report = compute_rosfin_match_metrics(expected, actual)

    assert report.precision == 0.0
    assert report.recall == 0.0
    assert report.f1 == 0.0
    assert report.true_positives == 0
    assert report.false_positives == 0
    assert report.true_negatives == 0
    assert report.false_negatives == 3


def test_mixed_results() -> None:
    """Test mixed results with some correct and some incorrect."""
    expected = [True, True, False, False]
    actual = [True, False, False, True]

    report = compute_rosfin_match_metrics(expected, actual)

    assert report.true_positives == 1
    assert report.false_negatives == 1
    assert report.true_negatives == 1
    assert report.false_positives == 1
    assert report.precision == 0.5
    assert report.recall == 0.5
    assert report.f1 == 0.5


def test_length_mismatch_raises_error() -> None:
    """Test that mismatched lengths raise ValueError."""
    expected = [True, False]
    actual = [True, False, True]

    with pytest.raises(ValueError, match="same length"):
        compute_rosfin_match_metrics(expected, actual)


def test_empty_lists() -> None:
    """Test empty lists."""
    expected: list[bool] = []
    actual: list[bool] = []

    report = compute_rosfin_match_metrics(expected, actual)

    assert report.total_cases == 0
    assert report.precision == 0.0
    assert report.recall == 0.0
    assert report.f1 == 0.0

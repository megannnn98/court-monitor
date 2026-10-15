"""Reading the relevance baseline: threshold curve, operating points chosen on validation,
error summaries, the model's strongest words, duplicates across splits."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sklearn")

from evaluation.relevance.analysis import (
    curve_thresholds,
    duplicates,
    fn_details,
    fn_summary,
    fp_by_kind,
    model_features,
    near_threshold,
    operating_points,
    threshold_curve,
    top_features,
)
from evaluation.relevance.baseline import Article, collect_errors, run

START = datetime(2026, 8, 1, tzinfo=UTC)


def article(
    article_id: int,
    label: int,
    *,
    title: str = "",
    text: str = "",
    kind: str = "",
    source: str = "Лента",
    stratum: str = "kept",
) -> Article:
    words = "возбуждено уголовное дело арестован" if label else "погода концерт футбол"
    return Article(
        article_id=article_id,
        title=title or f"Заголовок {article_id}",
        text=text or f"{words} {words} номер{article_id}",
        published_at=START + timedelta(days=article_id),
        label=label,
        kind=kind or ("case" if label else "other"),
        source=source,
        stratum=stratum,
    )


# --- threshold curve -----------------------------------------------------------------


def test_curve_counts_each_threshold() -> None:
    labels = [0, 0, 0, 1, 1, 1, 1]
    probabilities = [0.1, 0.2, 0.8, 0.3, 0.6, 0.7, 0.9]
    low, high = threshold_curve(labels, probabilities, [0.25, 0.65])
    assert (low["tn"], low["fp"], low["fn"], low["tp"]) == (2, 1, 0, 4)
    assert low["precision_case"] == pytest.approx(4 / 5)
    assert low["recall_case"] == 1.0
    assert low["f1_case"] == pytest.approx(2 * 0.8 / 1.8)
    assert low["accuracy"] == pytest.approx(6 / 7)
    assert (high["tn"], high["fp"], high["fn"], high["tp"]) == (2, 1, 2, 2)
    assert high["threshold"] == 0.65


def test_curve_with_no_predicted_case_has_zero_precision() -> None:
    (point,) = threshold_curve([1, 0], [0.2, 0.1], [0.5])
    assert (point["precision_case"], point["recall_case"], point["f1_case"]) == (0, 0, 0)


def test_curve_adds_the_chosen_threshold_once() -> None:
    assert curve_thresholds(0.3168, (0.1, 0.3, 0.5)) == [0.1, 0.3, 0.3168, 0.5]
    assert curve_thresholds(0.3, (0.1, 0.3, 0.5)) == [0.1, 0.3, 0.5]


def test_curve_refuses_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="2 labels but 1"):
        threshold_curve([1, 0], [0.5], [0.5])


# --- operating points ----------------------------------------------------------------

VALIDATION_LABELS = [1, 1, 1, 1, 0, 0, 0, 0, 1, 0]
VALIDATION_PROBABILITIES = [0.9, 0.8, 0.7, 0.3, 0.85, 0.6, 0.2, 0.1, 0.25, 0.65]
TEST_LABELS = [1, 0, 1, 0]
TEST_PROBABILITIES = [0.9, 0.8, 0.3, 0.1]


def test_operating_point_has_the_highest_precision_at_the_recall() -> None:
    (point,) = operating_points(
        VALIDATION_LABELS,
        VALIDATION_PROBABILITIES,
        TEST_LABELS,
        TEST_PROBABILITIES,
        targets=[0.8],
    )
    # recall 0.8 at 0.3 has precision 4/7; recall 1.0 at 0.25 has 5/8, higher.
    assert point["status"] == "reachable"
    assert point["threshold"] == 0.25
    assert point["validation"]["precision_case"] == pytest.approx(5 / 8)
    assert point["validation"]["recall_case"] == 1.0
    assert (point["test"]["tp"], point["test"]["fp"]) == (2, 1)


def test_operating_point_takes_the_higher_threshold_on_equal_precision() -> None:
    (point,) = operating_points([1, 1, 0], [0.7, 0.6, 0.1], [1], [0.9], targets=[0.5])
    # 0.7 and 0.6 both have precision 1.0 at recall ≥ 0.5.
    assert point["threshold"] == 0.7


def test_an_unreachable_target_is_marked_and_has_no_threshold() -> None:
    points = operating_points([1, 0], [0.9, 0.1], [1, 0], [0.9, 0.1], targets=[0.5, 1.01])
    assert points[0]["status"] == "reachable"
    assert points[1] == {"target_recall": 1.01, "status": "unreachable"}


def test_the_test_does_not_choose_an_operating_point() -> None:
    targets = [0.5, 0.8, 0.9, 1.0]
    first = operating_points(
        VALIDATION_LABELS, VALIDATION_PROBABILITIES, TEST_LABELS, TEST_PROBABILITIES, targets
    )
    second = operating_points(
        VALIDATION_LABELS, VALIDATION_PROBABILITIES, [0, 1, 0, 1], [0.05, 0.95, 0.5, 0.01], targets
    )
    assert [p["threshold"] for p in first] == [p["threshold"] for p in second]
    assert [p["validation"] for p in first] == [p["validation"] for p in second]
    assert [p["test"] for p in first] != [p["test"] for p in second]


# --- errors --------------------------------------------------------------------------


def test_margin_is_probability_minus_threshold() -> None:
    rows = [article(1, 1), article(2, 0)]
    false_negatives, false_positives = collect_errors(rows, [0.313, 0.4], 0.317)
    assert false_negatives[0]["margin"] == pytest.approx(-0.004)
    assert false_positives[0]["margin"] == pytest.approx(0.083)
    (detail,) = fn_details(rows, [0.313, 0.4], 0.317)
    assert detail["margin"] == pytest.approx(-0.004)


def test_false_positives_are_summed_up_by_kind() -> None:
    errors = [
        {"kind": "other", "probability": 0.4},
        {"kind": "foreign", "probability": 0.9},
        {"kind": "other", "probability": 0.5},
        {"kind": "other", "probability": 0.9},
        {"kind": "administrative", "probability": 0.5},
    ]
    summary = fp_by_kind(errors)
    assert [entry["kind"] for entry in summary] == ["other", "administrative", "foreign"]
    other = summary[0]
    assert other["count"] == 3
    assert other["share"] == pytest.approx(3 / 5)
    assert other["mean_probability"] == pytest.approx(0.6)
    assert other["median_probability"] == pytest.approx(0.5)
    assert sum(entry["count"] for entry in summary) == 5


def test_missed_cases_are_described_and_summed_up() -> None:
    rows = [
        article(1, 1, text="суд арестовал", source="ОВД", stratum="kept"),
        article(2, 1, text="без особых слов", source="ОВД", stratum="purged_plain"),
        article(3, 1, text="что-то", source="Лента", stratum="purged_plain"),
        article(4, 1),  # caught
        article(5, 0),  # not a case
        article(6, 1),  # exactly at the threshold: caught
    ]
    details = fn_details(rows, [0.2, 0.3, 0.1, 0.9, 0.05, 0.5], 0.5)
    assert [entry["article_id"] for entry in details] == [2, 1, 3]
    first = details[0]
    assert first["text_length"] == len("без особых слов")
    assert first["legal_words"] is False
    assert details[1]["legal_words"] is True
    assert first["stratum"] == "purged_plain"
    assert first["source"] == "ОВД"
    assert fn_summary(details) == {
        "source": {"ОВД": 2, "Лента": 1},
        "stratum": {"purged_plain": 2, "kept": 1},
        "legal_words": {"False": 2, "True": 1},
    }


def test_near_threshold_lists_the_closest_per_outcome() -> None:
    rows = [article(1, 1), article(2, 1), article(3, 0), article(4, 0), article(5, 1)]
    probabilities = [0.52, 0.47, 0.55, 0.2, 0.9]
    near = near_threshold(rows, probabilities, 0.5, limit=3)
    assert [entry["article_id"] for entry in near["all"]] == [1, 2, 3]
    assert [entry["article_id"] for entry in near["tp"]] == [1, 5]
    assert [entry["article_id"] for entry in near["fn"]] == [2]
    assert [entry["article_id"] for entry in near["fp"]] == [3]
    assert [entry["article_id"] for entry in near["tn"]] == [4]
    assert near["fn"][0]["margin"] == pytest.approx(-0.03)
    assert set(near["all"][0]) == {
        "article_id",
        "title",
        "kind",
        "outcome",
        "probability",
        "margin",
    }


# --- features ------------------------------------------------------------------------


def test_top_features_are_ordered_by_weight_each_way() -> None:
    names = ["суд", "погода", "уголовное дело", "футбол", "статья", "и"]
    weights = [1.5, -2.0, 1.5, -0.5, 0.7, 0.0]
    features = top_features(names, weights, limit=2)
    assert features["case"] == [
        {"feature": "суд", "weight": 1.5},
        {"feature": "уголовное дело", "weight": 1.5},
    ]
    assert features["not_case"] == [
        {"feature": "погода", "weight": -2.0},
        {"feature": "футбол", "weight": -0.5},
    ]


def test_top_features_never_put_a_word_on_the_wrong_side() -> None:
    features = top_features(["a", "b"], [0.3, 0.1], limit=5)
    assert [pair["feature"] for pair in features["case"]] == ["a", "b"]
    assert features["not_case"] == []


def test_top_features_of_a_fitted_model_follow_its_coefficients() -> None:
    train = [article(i, int(i % 3 == 0)) for i in range(40)]
    test = [article(100 + i, int(i % 3 == 0)) for i in range(6)]
    report = run(train, test)
    features = model_features(report.fitted.model, limit=5)
    case_weights = [pair["weight"] for pair in features["case"]]
    not_case_weights = [pair["weight"] for pair in features["not_case"]]
    assert case_weights == sorted(case_weights, reverse=True)
    assert not_case_weights == sorted(not_case_weights)
    assert any("уголовное" in pair["feature"] for pair in features["case"])
    assert any("погода" in pair["feature"] for pair in features["not_case"])


# --- duplicates ----------------------------------------------------------------------


def test_duplicates_across_splits_name_both_sides() -> None:
    train = [
        article(1, 1, text="один текст", title="Общий заголовок"),
        article(2, 0, text="второй текст"),
        article(3, 0, text="второй текст"),  # repeat inside train: not a leak
    ]
    validation = [article(4, 0, text="третий текст", title="Общий заголовок")]
    test = [article(5, 0, text="один текст")]
    report = duplicates({"train": train, "validation": validation, "test": test})
    assert report["exact_duplicate_texts"] == [
        {
            "text": "один текст",
            "a": {"article_id": 1, "split": "train", "label": 1},
            "b": {"article_id": 5, "split": "test", "label": 0},
        }
    ]
    assert report["exact_duplicate_titles"] == [
        {
            "title": "Общий заголовок",
            "a": {"article_id": 1, "split": "train", "label": 1},
            "b": {"article_id": 4, "split": "validation", "label": 0},
        }
    ]
    assert report["counts"] == {"texts": {"test/train": 1}, "titles": {"train/validation": 1}}

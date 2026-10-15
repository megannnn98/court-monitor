"""The relevance baseline's evaluation: a split by time, a threshold chosen on validation,
the test scored once and never consulted before."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sklearn")

from evaluation.relevance import baseline
from evaluation.relevance.baseline import (
    Article,
    calculate_metrics,
    collect_errors,
    load_dataset,
    run,
    select_threshold,
    temporal_split,
    write_results,
)

START = datetime(2026, 8, 1, tzinfo=UTC)

CASE_WORDS = "возбуждено уголовное дело статья обвиняемый арестован суд"
OTHER_WORDS = "погода концерт футбол выставка рецепт фестиваль"


def article(article_id: int, label: int, *, day: int | None = None, text: str = "") -> Article:
    words = CASE_WORDS if label else OTHER_WORDS
    return Article(
        article_id=article_id,
        title=f"Заголовок {article_id}",
        text=text or f"{words} {words} номер{article_id}",
        published_at=START + timedelta(days=article_id if day is None else day),
        label=label,
        kind="case" if label else "other",
        source="Медиазона" if label else "Лента",
        stratum="kept" if label else "purged_plain",
    )


def corpus(size: int, first_id: int = 0) -> list[Article]:
    """Every third article a case; ids and days grow together."""
    return [article(first_id + i, int(i % 3 == 0)) for i in range(size)]


# --- split ---------------------------------------------------------------------------


def test_split_keeps_the_past_for_training_and_the_future_for_validation() -> None:
    rows = corpus(20)
    train, validation = temporal_split(list(reversed(rows)))
    assert len(train) == 16
    assert len(validation) == 4
    assert max(row.published_at for row in train) <= min(row.published_at for row in validation)
    assert [row.article_id for row in train] == list(range(16))
    assert [row.article_id for row in validation] == list(range(16, 20))


def test_split_does_not_depend_on_the_input_order() -> None:
    rows = [replace(row, published_at=START) for row in corpus(10)]
    assert temporal_split(rows) == temporal_split(list(reversed(rows)))


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([], "too few"),
        ([article(0, 1)], "too few"),
        ([article(i, 1) for i in range(10)], "train: needs both classes"),
        ([article(i, int(i == 0)) for i in range(10)], "validation: needs both classes"),
    ],
)
def test_split_refuses_what_it_cannot_split(rows: list[Article], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        temporal_split(rows)


@pytest.mark.parametrize("share", [0.0, 1.0, -0.1])
def test_split_refuses_a_share_outside_the_interval(share: float) -> None:
    with pytest.raises(ValueError, match="validation share"):
        temporal_split(corpus(10), share)


# --- threshold -----------------------------------------------------------------------


def test_threshold_takes_the_most_precise_of_those_reaching_the_recall() -> None:
    labels = [1, 1, 1, 1, 0, 0, 0, 0, 1, 0]
    probabilities = [0.9, 0.8, 0.7, 0.3, 0.85, 0.6, 0.2, 0.1, 0.25, 0.65]
    # recall 0.8 first reached at 0.3 (precision 4/7); at 0.25 recall 1.0 but 5/8 = 0.625.
    choice = select_threshold(labels, probabilities, target_recall=0.8)
    assert choice.threshold == 0.25
    assert choice.recall == 1.0
    assert choice.precision == pytest.approx(5 / 8)
    assert choice.target_met


def test_threshold_prefers_the_higher_threshold_on_equal_precision() -> None:
    # 0.7 and 0.6 both give precision 1.0 at recall ≥ 0.5: the higher one is taken.
    choice = select_threshold([1, 1], [0.7, 0.6], target_recall=0.5)
    assert choice.threshold == 0.7
    assert choice.recall == 0.5


def test_threshold_falls_back_to_the_highest_recall_and_says_so() -> None:
    labels = [1, 0, 1, 0]
    probabilities = [0.9, 0.8, 0.7, 0.6]
    choice = select_threshold(labels, probabilities, target_recall=1.01)
    assert not choice.target_met
    assert choice.recall == 1.0
    # Recall 1.0 at 0.7 (2/3) and 0.6 (2/4): the more precise is taken.
    assert choice.threshold == 0.7
    assert choice.precision == pytest.approx(2 / 3)


@pytest.mark.parametrize(
    ("labels", "probabilities", "message"),
    [
        ([], [], "no labels"),
        ([1, 0], [0.5], "2 labels but 1"),
        ([0, 0], [0.5, 0.4], "no positive"),
    ],
)
def test_threshold_refuses_bad_input(
    labels: list[int], probabilities: list[float], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        select_threshold(labels, probabilities)


# --- metrics and errors --------------------------------------------------------------


def test_confusion_matrix_is_laid_out_as_tn_fp_fn_tp() -> None:
    labels = [0, 0, 0, 1, 1, 1, 1]
    probabilities = [0.1, 0.2, 0.8, 0.3, 0.6, 0.7, 0.9]
    metrics = calculate_metrics(labels, probabilities, 0.5)
    assert (metrics["tn"], metrics["fp"], metrics["fn"], metrics["tp"]) == (2, 1, 1, 3)
    assert metrics["confusion_matrix"] == [[2, 1], [1, 3]]
    assert metrics["precision"] == pytest.approx(3 / 4)
    assert metrics["recall"] == pytest.approx(3 / 4)
    assert metrics["accuracy"] == pytest.approx(5 / 7)


def test_a_probability_at_the_threshold_is_a_case() -> None:
    metrics = calculate_metrics([1, 0], [0.5, 0.4], 0.5)
    assert (metrics["tp"], metrics["fn"]) == (1, 0)


def test_errors_are_misses_and_false_alarms_from_the_highest_probability() -> None:
    rows = [article(1, 1), article(2, 1), article(3, 0), article(4, 0), article(5, 1)]
    probabilities = [0.2, 0.45, 0.9, 0.6, 0.8]
    false_negatives, false_positives = collect_errors(rows, probabilities, 0.5)
    assert [e["article_id"] for e in false_negatives] == [2, 1]
    assert [e["article_id"] for e in false_positives] == [3, 4]
    assert false_negatives[0] == {
        "article_id": 2,
        "title": "Заголовок 2",
        "published_at": rows[1].published_at.isoformat(),
        "kind": "case",
        "source": "Медиазона",
        "probability": 0.45,
        "threshold": 0.5,
        "margin": pytest.approx(-0.05),
    }


def test_errors_refuse_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="2 articles but 1"):
        collect_errors([article(1, 1), article(2, 0)], [0.5], 0.5)


# --- loading -------------------------------------------------------------------------


def _line(**overrides: Any) -> str:
    row: dict[str, Any] = {
        "article_id": 1,
        "title": "t",
        "text": "x",
        "published_at": "2026-08-01T10:00:00+00:00",
        "label": 1,
        "kind": "case",
        "source": "s",
        "stratum": "kept",
    }
    row.update(overrides)
    return json.dumps(row, ensure_ascii=False)


def test_load_reads_articles(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    path.write_text(_line() + "\n\n" + _line(article_id=2, label=0) + "\n", encoding="utf-8")
    rows = load_dataset(path)
    assert [(row.article_id, row.label) for row in rows] == [(1, 1), (2, 0)]
    assert rows[0].published_at == datetime(2026, 8, 1, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "no articles"),
        ("\n  \n", "no articles"),
        ("{not json\n", "a.jsonl:1: not JSON"),
        (_line(label=2), "label must be 0 or 1"),
        (_line(label=True), "label must be 0 or 1"),
        (_line(published_at="вчера"), "bad published_at"),
        (_line(published_at="2026-08-01T10:00:00"), "without a time zone"),
        (json.dumps({"article_id": 1}), "missing title"),
    ],
)
def test_load_refuses_malformed_files(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "a.jsonl"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_dataset(path)


def test_load_refuses_a_line_that_is_not_an_object(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    path.write_text("[1, 2]\n", encoding="utf-8")
    with pytest.raises(TypeError, match="a.jsonl:1: not a JSON object"):
        load_dataset(path)


# --- the whole run -------------------------------------------------------------------


def test_the_test_is_never_fitted_on(monkeypatch: pytest.MonkeyPatch) -> None:
    fitted: list[list[str]] = []
    real = baseline.build_model

    def recording() -> Any:
        model = real()
        original = model.fit

        def fit(texts: list[str], labels: list[int]) -> Any:
            fitted.append(list(texts))
            return original(texts, labels)

        model.fit = fit
        return model

    train_rows = corpus(40)
    test_rows = corpus(15, first_id=100)
    run(train_rows, test_rows, model_factory=recording)
    train, _ = temporal_split(train_rows)
    assert fitted == [[baseline.article_text(row) for row in train]]


def test_the_test_does_not_move_the_threshold_or_validation() -> None:
    train_rows = corpus(40)
    test_rows = corpus(15, first_id=100)
    flipped = [replace(row, label=1 - row.label, text=f"{OTHER_WORDS} другое") for row in test_rows]
    flipped[0] = replace(flipped[0], label=1)
    flipped[1] = replace(flipped[1], label=0)
    first = run(train_rows, test_rows)
    second = run(train_rows, flipped)
    assert first.threshold == second.threshold
    assert first.validation == second.validation
    assert first.test != second.test


def test_the_run_is_reproducible() -> None:
    train_rows = corpus(40)
    test_rows = corpus(15, first_id=100)
    assert run(train_rows, test_rows) == run(train_rows, test_rows)


def test_a_run_without_test_articles_is_refused() -> None:
    with pytest.raises(ValueError, match="no test articles"):
        run(corpus(40), [])


def test_smoke_from_dataset_to_error_analysis(tmp_path: Path) -> None:
    """dataset → split → fit → predict_proba → threshold → metrics → error analysis."""
    train_rows = corpus(40)
    # A case worded as noise and noise worded as a case: one FN and one FP at least.
    test_rows = [
        *corpus(12, first_id=100),
        article(200, 1, text=f"{OTHER_WORDS} {OTHER_WORDS}"),
        article(201, 0, text=f"{CASE_WORDS} {CASE_WORDS}"),
    ]
    source = tmp_path / "train.jsonl"
    source.write_text(
        "".join(
            json.dumps({**row.__dict__, "published_at": row.published_at.isoformat()}) + "\n"
            for row in train_rows
        ),
        encoding="utf-8",
    )
    report = run(load_dataset(source), test_rows)

    assert report.sizes == {"train": 32, "validation": 8, "test": 14}
    assert report.threshold.target_met
    assert report.validation["recall"] >= 0.9
    assert report.test["tn"] + report.test["fp"] + report.test["fn"] + report.test["tp"] == 14
    assert [e["article_id"] for e in report.false_negatives] == [200]
    assert [e["article_id"] for e in report.false_positives] == [201]

    out = tmp_path / "results"
    write_results(report, out)
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["sizes"] == report.sizes
    assert metrics["threshold"]["threshold"] == report.threshold.threshold
    assert metrics["errors"] == {"false_negatives": 1, "false_positives": 1}
    for name, errors in (
        ("false_negatives.jsonl", report.false_negatives),
        ("false_positives.jsonl", report.false_positives),
    ):
        lines = (out / name).read_text(encoding="utf-8").splitlines()
        assert [json.loads(line) for line in lines] == errors

"""The gate against the frozen baseline: what it removes is counted by category, and the
outer validation is touched only when the gate wins the temporal CV."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sklearn")

from evaluation.relevance.baseline import Article
from evaluation.relevance.gate_experiment import compare, improves, run

ADMIN = "Суд оштрафовал жительницу по КоАП за пост."
CASE = "Активиста приговорили к колонии по уголовному делу."


def article(i: int, text: str, label: int) -> Article:
    return Article(
        article_id=i,
        title="t",
        text=text,
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
        label=label,
        kind="llm_consensus",
        source="s",
        stratum="",
    )


def test_the_gate_removes_false_alarms_and_counts_them() -> None:
    rows = [article(1, ADMIN, 0), article(2, CASE, 1), article(3, CASE, 0)]
    result = compare(rows, [0.9, 0.9, 0.9], 0.5)
    assert result["baseline"]["fp"] == 2
    assert result["gate_baseline"]["fp"] == 1
    assert result["fp_removed_by_category"] == {"administrative": 1}
    assert result["relevant_dropped"] == []
    assert improves(result)


def test_a_relevant_article_ruled_out_is_reported_and_fails() -> None:
    rows = [article(1, ADMIN, 1), article(2, CASE, 1), article(3, CASE, 0)]
    result = compare(rows, [0.9, 0.9, 0.9], 0.5)
    assert [r["article_id"] for r in result["relevant_dropped"]] == [1]
    assert result["gate_recall"] == 0.5
    assert not improves(result)


def test_no_gain_in_precision_is_no_improvement() -> None:
    rows = [article(1, CASE, 0), article(2, CASE, 1)]
    assert not improves(compare(rows, [0.9, 0.9], 0.5))


def test_the_outer_validation_is_not_read_without_a_gain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evaluation.relevance import gate_experiment as experiment

    rows = [article(i, CASE, i % 3 == 0) for i in range(8)]
    read: list[str] = []

    def load(path: Path) -> list[Article]:
        read.append(path.name)
        return rows

    def folds(*_: Any) -> list[tuple[list[Article], list[Article], dict[str, Any]]]:
        return [(rows[:4], rows[4:], {"fold": 1})]

    class Model:
        def fit(self, *_: Any) -> None:
            return None

        def predict_proba(self, texts: list[str]) -> Any:
            import numpy

            return numpy.array([[0.1, 0.9] for _ in texts])

    monkeypatch.setattr(experiment, "load_split", load)
    monkeypatch.setattr(experiment, "temporal_folds", folds)
    monkeypatch.setattr(experiment, "text_hashes", lambda _: {})
    report = run(tmp_path / "train.jsonl", tmp_path / "validation.jsonl", 0.5, str, Model)
    assert not report["gate_improves_cv"]
    assert read == ["train.jsonl"]
    assert "outer_validation" not in report


def test_the_outer_validation_is_not_read_unless_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evaluation.relevance import gate_experiment as experiment

    rows = [article(1, ADMIN, 0), article(2, CASE, 1), article(3, CASE, 0), article(4, CASE, 1)]
    read: list[str] = []

    def load(path: Path) -> list[Article]:
        read.append(path.name)
        return rows

    class Model:
        def fit(self, *_: Any) -> None:
            return None

        def predict_proba(self, texts: list[str]) -> Any:
            import numpy

            return numpy.array([[0.1, 0.9] for _ in texts])

    monkeypatch.setattr(experiment, "load_split", load)
    monkeypatch.setattr(experiment, "temporal_folds", lambda *_: [(rows, rows, {"fold": 1})])
    monkeypatch.setattr(experiment, "text_hashes", lambda _: {})
    report = run(tmp_path / "train.jsonl", tmp_path / "validation.jsonl", 0.5, str, Model)
    assert report["gate_improves_cv"]
    assert read == ["train.jsonl"]
    assert "outer_validation" not in report

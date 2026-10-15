"""The relevance baseline on the Court Monitor target: four preprocessings judged on the
development validation, a threshold at recall 0.90 without fallback, and never a read of
the frozen holdout."""

from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sklearn")

from evaluation.relevance import court_relevance_baseline as experiment
from evaluation.relevance.court_relevance_baseline import (
    HoldoutAccessError,
    choose,
    errors,
    guarded,
    load_split,
    run,
    sensitivity,
)

FIELD = "relevant_to_court_monitor"
CASE = "активиста арестовали за антивоенный пост суд приговор"
OTHER = "погода концерт футбол убийство мошенничество"


def row(article_id: int, relevant: bool, source: str = "llm_consensus") -> dict[str, Any]:
    words = CASE if relevant else OTHER
    return {
        "article_id": article_id,
        "title": f"Заголовок {article_id}",
        "text": f"{words} {words} номер{article_id}",
        "source": "Медиазона",
        "url": "u",
        "published_at": f"2026-08-{1 + article_id % 28:02d}T00:00:00+00:00",
        "text_hash": str(article_id),
        FIELD: relevant,
        "annotation_source": source,
    }


def split_files(tmp_path: Path) -> tuple[Path, Path]:
    train = [row(i, i % 3 == 0) for i in range(60)]
    validation = [
        row(100 + i, i % 3 == 0, "assistant_adjudication" if i % 5 == 0 else "llm_consensus")
        for i in range(30)
    ]
    paths = (tmp_path / "train.jsonl", tmp_path / "validation.jsonl")
    for path, rows in zip(paths, (train, validation), strict=True):
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return paths


def test_the_holdout_is_refused(tmp_path: Path) -> None:
    holdout = tmp_path / "holdout"
    (holdout / "court_monitor_relevance").mkdir(parents=True)
    with pytest.raises(HoldoutAccessError):
        guarded(holdout / "court_monitor_relevance" / "final_labels.jsonl", holdout)
    with pytest.raises(HoldoutAccessError):
        guarded(holdout, holdout)
    assert guarded(tmp_path / "train.jsonl", holdout) == tmp_path / "train.jsonl"
    with pytest.raises(HoldoutAccessError):
        load_split(experiment.HOLDOUT_DIR / "court_monitor_relevance" / "final_labels.jsonl")


def test_the_experiment_never_opens_the_holdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    train, validation = split_files(tmp_path)
    holdout = str(experiment.HOLDOUT_DIR.resolve())
    real_open, real_read = builtins.open, Path.read_text

    def no_holdout_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(Path(str(file)).resolve()).startswith(holdout):
            raise AssertionError(f"opened {file}")
        return real_open(file, *args, **kwargs)

    def no_holdout_read(self: Path, *args: Any, **kwargs: Any) -> str:
        if str(self.resolve()).startswith(holdout):
            raise AssertionError(f"read {self}")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", no_holdout_open)
    monkeypatch.setattr(Path, "read_text", no_holdout_read)
    report = run(train, validation)
    assert report["holdout_read"] is False
    assert set(report["variants"]) == {"raw", "cleanup", "lemma", "cleanup_lemma"}
    assert report["selected"] in report["variants"]
    assert report["selection_source"] == "validation"


def test_every_variant_is_judged_the_same_way(tmp_path: Path) -> None:
    train, validation = split_files(tmp_path)
    report = run(train, validation)
    for result in report["variants"].values():
        assert result["at_0_5"]["tp"] + result["at_0_5"]["fn"] == 10
        assert [p["target_recall"] for p in result["operating_points"]] == [0.8, 0.85, 0.9, 0.95]
        if result["target_met"]:
            assert result["at_target"]["recall"] >= 0.9
    assert report["sensitivity"]["assistant_adjudication_in_validation"] == 6
    assert report["sensitivity"]["without_assistant_adjudication"]["total"] == 24


def test_no_variant_at_the_recall_is_none_not_a_fallback() -> None:
    results: dict[str, dict[str, Any]] = {
        "raw": {"target_met": False, "at_target": None},
        "cleanup": {"target_met": False, "at_target": None},
    }
    assert choose(results) is None
    results["cleanup"] = {
        "target_met": True,
        "at_target": {"precision": 0.4, "average_precision": 0.5},
    }
    assert choose(results) == "cleanup"


def test_the_choice_breaks_ties_by_pr_auc_then_simplicity() -> None:
    def met(precision: float, ap: float) -> dict[str, Any]:
        return {"target_met": True, "at_target": {"precision": precision, "average_precision": ap}}

    assert choose({"raw": met(0.5, 0.6), "lemma": met(0.5, 0.7)}) == "lemma"
    assert choose({"raw": met(0.5, 0.6), "lemma": met(0.5, 0.6)}) == "raw"
    assert choose({"raw": met(0.5, 0.9), "lemma": met(0.6, 0.1)}) == "lemma"


def test_errors_and_sensitivity_use_one_threshold(tmp_path: Path) -> None:
    _, validation = split_files(tmp_path)
    rows = load_split(validation)
    probabilities = [0.9 if r.label else 0.1 for r in rows]
    probabilities[0], probabilities[1] = 0.2, 0.8  # a miss and a false alarm
    found = errors(rows, probabilities, 0.5)
    assert [e["article_id"] for e in found["false_negatives"]] == [rows[0].article_id]
    assert [e["article_id"] for e in found["false_positives"]] == [rows[1].article_id]
    assert found["false_negatives"][0]["margin"] == pytest.approx(-0.3)
    assert found["false_negatives"][0]["annotation_source"] == rows[0].kind
    report = sensitivity(rows, probabilities, 0.5)
    assert report["full"]["threshold"] == report["without_assistant_adjudication"]["threshold"]
    assert report["full"]["total"] == 30

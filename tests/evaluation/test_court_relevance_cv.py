"""Temporal cross-validation inside the development train: expanding-window folds, no
duplicate across a boundary, a stop on a thin fold, and an outer validation that decides
nothing."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sklearn")

from evaluation.relevance.baseline import Article
from evaluation.relevance.court_relevance_cv import (
    TooFewRelevantError,
    run,
    temporal_folds,
)
from evaluation.relevance.preprocessing import cleanup, raw

FIELD = "relevant_to_court_monitor"
START = datetime(2026, 8, 1, tzinfo=UTC)
CASE = "активиста арестовали за антивоенный пост суд приговор"
OTHER = "погода концерт футбол убийство мошенничество"


def article(i: int, relevant: bool) -> Article:
    return Article(
        article_id=i,
        title=f"t{i}",
        text=CASE if relevant else OTHER,
        published_at=START + timedelta(hours=i),
        label=int(relevant),
        kind="llm_consensus",
        source="s",
        stratum="",
    )


def rows(n: int, every: int = 3) -> list[Article]:
    return [article(i, i % every == 0) for i in range(n)]


def test_each_fold_learns_only_on_the_past() -> None:
    data = rows(320)
    folds = temporal_folds(list(reversed(data)), {r.article_id: str(r.article_id) for r in data})
    assert len(folds) == 3
    for train, validation, info in folds:
        assert max(r.published_at for r in train) < min(r.published_at for r in validation)
        assert info["validation"]["relevant"] >= 20
    assert [len(f[0]) for f in folds] == [80, 160, 240]
    assert [len(f[1]) for f in folds] == [80, 80, 80]


def test_a_duplicate_of_the_validation_leaves_the_fold_train() -> None:
    data = rows(320)
    hashes = {r.article_id: str(r.article_id) for r in data}
    hashes[5] = hashes[100] = "twin"
    folds = temporal_folds(data, hashes)
    train, validation, info = folds[0]
    assert 100 in {r.article_id for r in validation}
    assert 5 not in {r.article_id for r in train}
    assert info["left_out_of_train_as_duplicates"] == [5]
    assert 5 in {r.article_id for r in folds[1][0]}


def test_a_thin_validation_fold_stops_everything() -> None:
    data = rows(120, every=7)
    with pytest.raises(TooFewRelevantError):
        temporal_folds(data, {r.article_id: str(r.article_id) for r in data})


def _write(path: Path, data: list[Article], flip: bool = False) -> Path:
    def as_row(r: Article) -> dict[str, Any]:
        return {
            "article_id": r.article_id,
            "title": r.title,
            "text": r.text + f" номер{r.article_id}",
            "source": r.source,
            "published_at": r.published_at.isoformat(),
            "text_hash": str(r.article_id),
            FIELD: (not r.label) if flip else bool(r.label),
            "annotation_source": r.kind,
        }

    path.write_text("".join(json.dumps(as_row(r), ensure_ascii=False) + "\n" for r in data))
    return path


def test_the_outer_validation_decides_nothing(tmp_path: Path) -> None:
    train = _write(tmp_path / "train.jsonl", rows(320))
    later = [article(1000 + i, i % 3 == 0) for i in range(60)]
    one = _write(tmp_path / "v1.jsonl", later)
    other = _write(tmp_path / "v2.jsonl", later, flip=True)
    variants = {"raw": raw, "cleanup": cleanup}
    first, second = run(train, one, variants), run(train, other, variants)
    assert first["selected"] == second["selected"]
    assert first["selected_threshold"] == second["selected_threshold"]
    assert first["cv"] == second["cv"]
    assert first["outer_validation"]["metrics"] != second["outer_validation"]["metrics"]


def test_the_outer_validation_is_read_only_after_the_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evaluation.relevance import court_relevance_cv as cv

    train = _write(tmp_path / "train.jsonl", rows(320))
    validation = _write(tmp_path / "v.jsonl", [article(1000 + i, i % 3 == 0) for i in range(60)])
    events: list[str] = []
    from evaluation.relevance.court_relevance_baseline import load_split as real_load

    real_cv = cv.cross_validate

    def load(path: Path) -> list[Article]:
        events.append(f"load {path.name}")
        return real_load(path)

    def cross_validate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        events.append("cv")
        return real_cv(*args, **kwargs)

    monkeypatch.setattr(cv, "load_split", load)
    monkeypatch.setattr(cv, "cross_validate", cross_validate)
    run(train, validation, {"raw": raw})
    assert events == ["load train.jsonl", "cv", "load v.jsonl"]

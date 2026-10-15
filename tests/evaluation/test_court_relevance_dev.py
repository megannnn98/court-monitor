"""The development corpus of `court-monitor-relevance-v1`: the old articles without their
old labels and without anything the frozen holdout holds."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evaluation.relevance.court_relevance_dev import (
    candidates,
    checked_articles,
    prepare,
    without_holdout,
)
from evaluation.relevance.holdout import FrozenError, read_jsonl, text_hash

WHEN = datetime(2026, 10, 15, tzinfo=UTC)


def old(article_id: int, text: str = "", title: str = "") -> dict[str, Any]:
    return {
        "article_id": article_id,
        "title": title or f"Заголовок {article_id}",
        "text": text or f"Старый текст {article_id}",
        "source": "s",
        "url": "u",
        "published_at": f"2026-08-{1 + article_id % 28:02d}T00:00:00+00:00",
        "label": 1,
        "kind": "case",
        "political": True,
        "stratum": "kept",
    }


def held(article_id: int, text: str, title: str = "") -> dict[str, Any]:
    return {"article_id": article_id, "title": title or f"Новый {article_id}", "text": text}


def test_old_labels_are_not_carried() -> None:
    (row,) = candidates([old(1)])
    assert set(row) == {"article_id", "title", "text", "source", "url", "published_at", "text_hash"}


def test_anything_of_the_holdout_is_left_out() -> None:
    rows = candidates(
        [
            old(1),
            old(2, text="Суд арестовал активиста"),
            old(3, text="СУД  арестовал\nактивиста"),
            old(4, title="Главные новости к вечеру"),
            old(5),
        ]
    )
    holdout = [
        held(5, "совсем другой текст"),
        held(900, "Суд арестовал активиста"),
        held(901, "иное", title="Главные новости к вечеру"),
    ]
    kept, report = without_holdout(rows, holdout)
    assert [r["article_id"] for r in kept] == [1, 4]
    assert report == report | {
        "candidate_count": 5,
        "excluded_by_article_id": 1,
        "excluded_by_exact_text": 1,
        "excluded_by_normalized_hash": 2,
        "excluded": 3,
        "final_dev_candidate_count": 2,
        "duplicate_titles_not_excluded": 1,
        "excluded_article_ids": [2, 3, 5],
    }


def test_a_candidate_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="twice"):
        candidates([old(1), old(1)])


def layout(tmp_path: Path, holdout_texts: list[str]) -> tuple[list[Path], Path, Path]:
    files = [tmp_path / "train.jsonl", tmp_path / "test.jsonl"]
    files[0].write_text("".join(json.dumps(old(i)) + "\n" for i in range(1, 41)))
    files[1].write_text("".join(json.dumps(old(i)) + "\n" for i in range(41, 61)))
    holdout_dir = tmp_path / "holdout"
    (holdout_dir / "court_monitor_relevance").mkdir(parents=True)
    sample = [
        {**held(1000 + n, text), "text_hash": text_hash(text)}
        for n, text in enumerate(holdout_texts)
    ]
    (holdout_dir / "sample_articles.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sample)
    )
    (holdout_dir / "court_monitor_relevance" / "final_labels.jsonl").write_text(
        "".join(json.dumps({"article_id": r["article_id"]}) + "\n" for r in sample)
    )
    return files, holdout_dir, tmp_path / "dev"


def test_prepare_freezes_the_articles(tmp_path: Path) -> None:
    files, holdout_dir, dev = layout(tmp_path, ["Старый текст 7", "новое"])
    report = prepare(files, holdout_dir, dev, created_at=WHEN)
    assert report["final_dev_candidate_count"] == 59
    articles, digest = checked_articles(dev)
    assert 7 not in {a["article_id"] for a in articles}
    metadata = json.loads((dev / "metadata.json").read_text())
    assert metadata["old_labels_used"] is False
    assert metadata["articles_sha256"] == digest
    assert all("kind" not in a and "political" not in a for a in read_jsonl(dev / "articles.jsonl"))
    before = (dev / "articles.jsonl").read_bytes()
    with pytest.raises(FrozenError):
        prepare(files, holdout_dir, dev, created_at=WHEN)
    assert (dev / "articles.jsonl").read_bytes() == before


def test_many_leaks_stop_before_anything_is_written(tmp_path: Path) -> None:
    files, holdout_dir, dev = layout(tmp_path, [f"Старый текст {i}" for i in range(1, 10)])
    with pytest.raises(ValueError, match="stopped"):
        prepare(files, holdout_dir, dev, created_at=WHEN)
    assert not dev.exists()


def test_a_changed_dev_corpus_is_caught(tmp_path: Path) -> None:
    files, holdout_dir, dev = layout(tmp_path, ["новое"])
    prepare(files, holdout_dir, dev, created_at=WHEN)
    path = dev / "articles.jsonl"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="changed"):
        checked_articles(dev)

"""The development labels split by time: one boundary, a text on one side only, nothing
balanced. And the human tools pointed at the development corpus."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evaluation.relevance.annotate_court_monitor_relevance import main as annotate_main
from evaluation.relevance.court_relevance_dev import prepare
from evaluation.relevance.court_relevance_final import freeze_labels
from evaluation.relevance.court_relevance_split import describe, labelled, split, write_split
from evaluation.relevance.holdout import FrozenError, read_jsonl, text_hash

FIELD = "relevant_to_court_monitor"
WHEN = datetime(2026, 10, 15, tzinfo=UTC)


def row(
    article_id: int, day: int, relevant: bool, text: str = "", title: str = ""
) -> dict[str, Any]:
    body = text or f"Текст {article_id}"
    return {
        "article_id": article_id,
        "title": title or f"Заголовок {article_id}",
        "text": body,
        "published_at": f"2026-08-{day:02d}T12:00:00+00:00",
        "text_hash": text_hash(body),
        FIELD: relevant,
        "annotation_source": "llm_consensus",
    }


def test_one_boundary_by_time() -> None:
    rows = [row(i, i, i % 4 == 0) for i in range(1, 21)]
    train, validation, decided = split(list(reversed(rows)))
    assert decided["boundary"] == "2026-08-17T12:00:00Z"
    assert [r["article_id"] for r in train] == list(range(1, 17))
    assert [r["article_id"] for r in validation] == list(range(17, 21))
    assert max(r["published_at"] for r in train) < min(r["published_at"] for r in validation)
    assert decided["moved_duplicate_groups"] == []


def test_articles_at_the_boundary_time_all_go_to_validation() -> None:
    rows = [row(i, min(i, 15), i % 2 == 0) for i in range(1, 21)]
    train, validation, decided = split(rows)
    assert decided["boundary"] == "2026-08-15T12:00:00Z"
    assert [r["article_id"] for r in train] == list(range(1, 15))
    assert len(validation) == 6


def test_a_text_cut_by_the_boundary_goes_whole_to_validation() -> None:
    rows = [row(i, i, False) for i in range(1, 21)]
    rows[2] = row(3, 3, True, text="Один и тот же пост")
    rows[18] = row(19, 19, True, text="один  и тот же ПОСТ")
    rows[4] = row(5, 5, False, title="Главные новости")
    rows[19] = row(20, 20, False, title="Главные новости")
    train, validation, decided = split(rows)
    assert 3 not in {r["article_id"] for r in train}
    assert 3 in {r["article_id"] for r in validation}
    assert decided["moved_duplicate_groups"] == [
        {"text_hash": text_hash("Один и тот же пост"), "article_ids": [3, 19], "to": "validation"}
    ]
    assert 5 in {r["article_id"] for r in train}
    assert decided["titles_on_both_sides"] == 1
    assert decided["normalized_duplicate_groups"] == 1


def test_nothing_is_balanced() -> None:
    rows = [row(i, 1 + i % 28, i % 10 == 0) for i in range(1, 101)]
    train, validation, _ = split(rows)
    assert len(train) + len(validation) == 100
    assert sum(r[FIELD] for r in train + validation) == 10


def test_labels_must_match_the_articles() -> None:
    articles = [{k: v for k, v in row(i, i, False).items() if k != FIELD} for i in (1, 2)]
    labels = [{"article_id": 1, FIELD: True, "annotation_source": "human_adjudication"}]
    with pytest.raises(ValueError, match="1 unlabelled"):
        labelled(articles, labels)
    labels.append({"article_id": 2, FIELD: False, "annotation_source": "llm_consensus"})
    rows = labelled(articles, labels)
    assert [(r["article_id"], r[FIELD]) for r in rows] == [(1, True), (2, False)]


def test_the_split_is_frozen(tmp_path: Path) -> None:
    rows = [row(i, i, i % 3 == 0) for i in range(1, 21)]
    train, validation, decided = split(rows)
    metadata = write_split(tmp_path, train, validation, decided, {"x": 1}, created_at=WHEN)
    assert metadata["train"] == describe(train)
    assert metadata["validation"]["total"] == 4
    assert metadata["validation_relevant_below_minimum"] is True
    assert (metadata["balanced"], metadata["shuffled"], metadata["stratified"]) == (
        False,
        False,
        False,
    )
    again = write_split(tmp_path, train, validation, decided, {"x": 1}, created_at=WHEN)
    assert again == metadata
    with pytest.raises(FrozenError):
        write_split(tmp_path, train[1:], validation, decided, {"x": 1}, created_at=WHEN)
    assert read_jsonl(tmp_path / "train.jsonl") == train


# --- the human tools and the freeze on the development corpus ------------------------


def dev_corpus(tmp_path: Path) -> Path:
    def old(i: int) -> dict[str, Any]:
        return {
            "article_id": i,
            "title": f"T{i}",
            "text": f"Старый текст {i}",
            "source": "s",
            "url": "u",
            "published_at": f"2026-08-{1 + i % 28:02d}T00:00:00+00:00",
            "kind": "case",
            "political": True,
        }

    old_file = tmp_path / "old.jsonl"
    old_file.write_text("".join(json.dumps(old(i)) + "\n" for i in range(1, 121)))
    holdout = tmp_path / "holdout"
    (holdout / "court_monitor_relevance").mkdir(parents=True)
    sample = {"article_id": 9999, "title": "x", "text": "x", "text_hash": text_hash("x")}
    (holdout / "sample_articles.jsonl").write_text(json.dumps(sample) + "\n")
    (holdout / "court_monitor_relevance" / "final_labels.jsonl").write_text(
        json.dumps({"article_id": 9999}) + "\n"
    )
    dev = tmp_path / "dev"
    prepare([old_file], holdout, dev, created_at=WHEN)
    ids = [a["article_id"] for a in read_jsonl(dev / "articles.jsonl")]
    for name, flip in (("labels_a.jsonl", set()), ("labels_b.jsonl", {ids[0]})):
        (dev / name).write_text(
            "".join(
                json.dumps({"article_id": i, FIELD: (i % 2 == 0) != (i in flip)}) + "\n"
                for i in ids
            )
        )
    (dev / "annotation_run.json").write_text(json.dumps({"model_a": "a", "model_b": "b"}))
    return dev


def answers(*keys: str) -> Any:
    queue: Iterator[str] = iter(keys)
    return lambda prompt: next(queue)


def test_the_human_tools_work_on_the_dev_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev = dev_corpus(tmp_path)
    monkeypatch.setattr("evaluation.relevance.annotate_court_monitor_relevance.DEV_DIR", dev)
    keys = iter(["y", *(["y"] * 50)])
    monkeypatch.setattr("builtins.input", lambda prompt: next(keys))
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)
    assert annotate_main(["--dev", "--disagreements"]) == 0
    assert len(read_jsonl(dev / "adjudications.jsonl")) == 1
    assert annotate_main(["--dev", "--audit"]) == 0
    assert (dev / "audit_sample.json").exists()
    assert not (tmp_path / "holdout" / "court_monitor_relevance" / "audit_sample.json").exists()


def test_dev_labels_freeze_like_the_holdouts(tmp_path: Path) -> None:
    dev = dev_corpus(tmp_path)
    from evaluation.relevance.annotate_court_monitor_relevance import adjudicate, audit

    adjudicate(dev, dev / "articles.jsonl", ask=answers("n"), say=lambda _: None)
    consensus = {r["article_id"]: r[FIELD] for r in read_jsonl(dev / "consensus_labels.jsonl")}
    assert not (dev / "audit_sample.json").exists()
    from evaluation.relevance.court_relevance_consensus import freeze_audit

    drawn = freeze_audit(dev, dev / "articles.jsonl")
    audit(
        dev,
        dev / "articles.jsonl",
        ask=answers(*("y" if consensus[i] else "n" for i in drawn)),
        say=lambda _: None,
    )
    metadata = freeze_labels(
        dev,
        dev / "articles.jsonl",
        dataset="court-monitor-relevance-v1-dev",
        source={"articles_sha256": "abc"},
        created_at=WHEN,
    )
    assert metadata["dataset"] == "court-monitor-relevance-v1-dev"
    assert metadata["articles_sha256"] == "abc"
    assert metadata["article_count"] == 120
    assert metadata["sources"]["human_adjudication"] == 1


def test_assistant_decisions_are_allowed_for_dev_only_and_stay_apart(tmp_path: Path) -> None:
    from evaluation.relevance.court_relevance_consensus import HUMAN_ONLY, rebuild
    from evaluation.relevance.court_relevance_dev import DEV_ADJUDICATION_SOURCES

    dev = dev_corpus(tmp_path)
    ids = [a["article_id"] for a in read_jsonl(dev / "articles.jsonl")]
    (dev / "labels_b.jsonl").write_text(
        "".join(
            json.dumps({"article_id": i, FIELD: (i % 2 == 0) != (i in ids[:2])}) + "\n" for i in ids
        )
    )
    (dev / "adjudications.jsonl").write_text(
        json.dumps({"article_id": ids[0], FIELD: True, "annotation_source": "human_adjudication"})
        + "\n"
        + json.dumps(
            {"article_id": ids[1], FIELD: False, "annotation_source": "assistant_adjudication"}
        )
        + "\n"
    )
    with pytest.raises(ValueError, match="assistant_adjudication"):
        rebuild(dev, dev / "articles.jsonl", HUMAN_ONLY)
    counts = rebuild(dev, dev / "articles.jsonl", DEV_ADJUDICATION_SOURCES)
    assert counts["remaining"] == 0
    final = {r["article_id"]: r for r in read_jsonl(dev / "consensus_labels.jsonl")}
    assert final[ids[0]]["annotation_source"] == "human_adjudication"
    assert final[ids[1]]["annotation_source"] == "assistant_adjudication"
    from evaluation.relevance.court_relevance_final import final_labels

    rows = read_jsonl(dev / "consensus_labels.jsonl")
    with pytest.raises(ValueError, match="unknown source"):
        final_labels(read_jsonl(dev / "articles.jsonl"), rows, [])
    out = final_labels(read_jsonl(dev / "articles.jsonl"), rows, [], DEV_ADJUDICATION_SOURCES)
    assert {r["annotation_source"] for r in out} == {
        "llm_consensus",
        "human_adjudication",
        "assistant_adjudication",
    }

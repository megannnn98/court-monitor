"""The frozen final labels of `court-monitor-relevance-v1`: a person's word first, the set
whole, and no silent change after freezing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evaluation.relevance.court_relevance_consensus import freeze_audit
from evaluation.relevance.court_relevance_final import final_labels, freeze
from evaluation.relevance.holdout import FrozenError, read_jsonl, text_hash
from evaluation.relevance.sample_holdout import make_sample

FIELD = "relevant_to_court_monitor"
WHEN = datetime(2026, 10, 15, 12, tzinfo=UTC)


def article(article_id: int) -> dict[str, Any]:
    body = f"Текст {article_id}"
    return {
        "article_id": article_id,
        "title": f"Заголовок {article_id}",
        "text": body,
        "source": "s",
        "url": "u",
        "published_at": f"2026-09-{20 + article_id % 10:02d}T{article_id % 24:02d}:00:00Z",
        "text_hash": text_hash(body),
    }


def label(article_id: int, relevant: bool, source: str = "llm_consensus") -> dict[str, Any]:
    return {"article_id": article_id, FIELD: relevant, "annotation_source": source}


def verdict(article_id: int, consensus: bool, human: bool) -> dict[str, Any]:
    return {
        "article_id": article_id,
        "consensus": consensus,
        "human": human,
        "verdict": "agree" if consensus == human else "disagree",
    }


# --- the labels ----------------------------------------------------------------------


def test_a_person_comes_first() -> None:
    articles = [article(i) for i in (1, 2, 3, 4)]
    consensus = [
        label(1, True),
        label(2, False, "human_adjudication"),
        label(3, True),
        label(4, False),
    ]
    audit = [verdict(3, True, False), verdict(4, False, False)]
    final = {row["article_id"]: row for row in final_labels(articles, consensus, audit)}
    assert final[1] == label(1, True)
    assert final[2] == label(2, False, "human_adjudication")
    assert final[3] == label(3, False, "human_audit_correction")
    assert final[4] == label(4, False)


@pytest.mark.parametrize(
    ("consensus", "audit", "message"),
    [
        ([label(1, True)], [], "1 articles without a label"),
        ([label(1, True), label(2, True), label(2, True)], [], "labelled twice"),
        ([label(1, True), label(2, True), label(9, True)], [], "outside the sample"),
        ([label(1, True), label(2, True, "model")], [], "unknown source"),
        (
            [label(1, True), label(2, True, "human_adjudication")],
            [verdict(2, True, False)],
            "no consensus label",
        ),
        ([label(1, True), label(2, True)], [verdict(1, False, False)], "another consensus"),
        (
            [label(1, True), label(2, True)],
            [{**verdict(1, True, False), "verdict": "agree"}],
            "verdict and answers",
        ),
    ],
)
def test_a_set_that_is_not_whole_is_refused(
    consensus: list[dict[str, Any]], audit: list[dict[str, Any]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        final_labels([article(1), article(2)], consensus, audit)


# --- freezing ------------------------------------------------------------------------


def holdout(tmp_path: Path, size: int = 120) -> Path:
    rows = [article(21000 + i) for i in range(size * 2)]
    (tmp_path / "articles.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    (tmp_path / "metadata.json").write_text(json.dumps({"frozen": True}), encoding="utf-8")
    make_sample(tmp_path, size=size, created_at=WHEN)
    out = tmp_path / "court_monitor_relevance"
    out.mkdir()
    ids = [a["article_id"] for a in read_jsonl(tmp_path / "sample_articles.jsonl")]
    for name, flip in (("labels_a.jsonl", set()), ("labels_b.jsonl", {ids[0], ids[1]})):
        (out / name).write_text(
            "".join(
                json.dumps({"article_id": i, FIELD: (i % 2 == 0) != (i in flip)}) + "\n"
                for i in ids
            )
        )
    (out / "adjudications.jsonl").write_text(
        "".join(json.dumps(label(i, True, "human_adjudication")) + "\n" for i in (ids[0], ids[1]))
    )
    (out / "annotation_run.json").write_text(json.dumps({"model_a": "a", "model_b": "b"}))
    audited = freeze_audit(out, tmp_path / "sample_articles.jsonl")
    consensus = {r["article_id"]: r[FIELD] for r in read_jsonl(out / "consensus_labels.jsonl")}
    rows = [
        verdict(i, consensus[i], not consensus[i] if n < 3 else consensus[i])
        for n, i in enumerate(audited)
    ]
    (out / "audit.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return out


def test_the_frozen_set_and_its_metadata(tmp_path: Path) -> None:
    out = holdout(tmp_path)
    metadata = freeze(out, tmp_path, created_at=WHEN)
    labels = read_jsonl(out / "final_labels.jsonl")
    assert len(labels) == len({row["article_id"] for row in labels}) == 120
    corrected = [row for row in labels if row["annotation_source"] == "human_audit_correction"]
    assert len(corrected) == 3
    assert metadata["sources"] == {
        "llm_consensus": 115,
        "human_adjudication": 2,
        "human_audit_correction": 3,
    }
    assert metadata["relevant"] + metadata["not_relevant"] == 120
    assert metadata["audit"] == metadata["audit"] | {"size": 50, "agree": 47, "seed": 20261015}
    assert metadata["ab_agreement"] == {"agree": 118, "of": 120, "rate": 118 / 120}
    assert metadata["prompt_version"] == "court-monitor-relevance-v1"
    assert metadata["frozen"] is True
    on_disk = json.loads((out / "final_dataset_metadata.json").read_text(encoding="utf-8"))
    assert on_disk == metadata


def test_a_second_build_changes_nothing(tmp_path: Path) -> None:
    out = holdout(tmp_path)
    first = freeze(out, tmp_path, created_at=WHEN)
    before = {p.name: p.read_bytes() for p in out.iterdir() if p.name.startswith("final")}
    assert freeze(out, tmp_path, created_at=datetime(2027, 1, 1, tzinfo=UTC)) == first
    assert {p.name: p.read_bytes() for p in out.iterdir() if p.name.startswith("final")} == before


def test_a_build_that_would_differ_is_refused(tmp_path: Path) -> None:
    out = holdout(tmp_path)
    freeze(out, tmp_path, created_at=WHEN)
    before = (out / "final_labels.jsonl").read_bytes()
    rows = read_jsonl(out / "audit.jsonl")
    rows[3] = verdict(rows[3]["article_id"], rows[3]["consensus"], not rows[3]["consensus"])
    (out / "audit.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    with pytest.raises(FrozenError, match="differs"):
        freeze(out, tmp_path, created_at=WHEN)
    assert (out / "final_labels.jsonl").read_bytes() == before


def test_an_unfinished_audit_or_disagreement_is_refused(tmp_path: Path) -> None:
    out = holdout(tmp_path)
    rows = read_jsonl(out / "audit.jsonl")
    (out / "audit.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows[:-1]))
    with pytest.raises(ValueError, match="audit is not complete"):
        freeze(out, tmp_path, created_at=WHEN)
    (out / "adjudications.jsonl").write_text("")
    with pytest.raises(ValueError, match="2 disagreements are not decided"):
        freeze(out, tmp_path, created_at=WHEN)
    assert not (out / "final_labels.jsonl").exists()

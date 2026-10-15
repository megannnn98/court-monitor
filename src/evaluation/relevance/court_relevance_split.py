"""Train and validation of `court-monitor-relevance-v1` from the frozen development labels:
by time alone.

One boundary in `published_at`: before it train, from it on validation — the boundary
being the publication time of the article at the 80% mark, so that no article is picked
around it to hit 80/20 exactly. Nothing is shuffled, stratified or balanced: the classes
keep their natural share.

A text has one side. A group of articles with the same normalized text that the boundary
would cut goes whole to validation, the later side, and the metadata says which. Equal
titles are only counted.

Only the development corpus is read — never the frozen holdout of 400.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.relevance.holdout import FrozenError, ordered, write_atomically

FIELD = "relevant_to_court_monitor"
TRAIN_SHARE = 0.8
MIN_VALIDATION_RELEVANT = 50


def _when(value: str) -> datetime:
    when = datetime.fromisoformat(value)
    if when.tzinfo is None:
        raise ValueError(f"published_at without a time zone: {value!r}")
    return when.astimezone(UTC)


def labelled(
    articles: Sequence[Mapping[str, Any]], labels: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Each article with its final label; every article must have exactly one."""
    by_id = {int(row["article_id"]): row for row in labels}
    if len(by_id) != len(labels):
        raise ValueError("an article is labelled twice")
    ids = {int(article["article_id"]) for article in articles}
    if ids != set(by_id):
        raise ValueError(
            f"labels and articles differ: {len(ids - set(by_id))} unlabelled, "
            f"{len(set(by_id) - ids)} unknown"
        )
    return [
        {
            **dict(article),
            FIELD: bool(by_id[int(article["article_id"])][FIELD]),
            "annotation_source": by_id[int(article["article_id"])]["annotation_source"],
        }
        for article in ordered(articles)
    ]


def split(
    rows: Sequence[Mapping[str, Any]], train_share: float = TRAIN_SHARE
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Train, validation and what was decided: the boundary, the groups moved."""
    if not 0 < train_share < 1:
        raise ValueError(f"train share must be within (0, 1), got {train_share}")
    rows = ordered(rows)
    if len(rows) < 2:
        raise ValueError("too few articles to split")
    boundary = _when(rows[min(math.ceil(len(rows) * train_share), len(rows) - 1)]["published_at"])
    side = {int(r["article_id"]): _when(r["published_at"]) >= boundary for r in rows}

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["text_hash"]].append(dict(row))
    moved = []
    for text_hash, members in groups.items():
        if len({side[int(m["article_id"])] for m in members}) > 1:
            moved.append(
                {
                    "text_hash": text_hash,
                    "article_ids": [int(m["article_id"]) for m in members],
                    "to": "validation",
                }
            )
            for member in members:
                side[int(member["article_id"])] = True
    exact: dict[str, set[bool]] = defaultdict(set)
    for row in rows:
        exact[row["text"]].add(side[int(row["article_id"])])
    if any(len(sides) > 1 for sides in exact.values()):
        raise AssertionError("an exact text on both sides after moving the groups")

    train = [dict(r) for r in rows if not side[int(r["article_id"])]]
    validation = [dict(r) for r in rows if side[int(r["article_id"])]]
    if not train or not validation:
        raise ValueError("the boundary leaves a side empty")
    train_titles = {r["title"].strip() for r in train}
    decided = {
        "boundary": boundary.isoformat().replace("+00:00", "Z"),
        "rule": "published_at < boundary -> train; published_at >= boundary -> validation",
        "train_share_target": train_share,
        "moved_duplicate_groups": moved,
        "normalized_duplicate_groups": sum(1 for members in groups.values() if len(members) > 1),
        "exact_duplicate_groups": sum(
            1 for count in Counter(r["text"] for r in rows).values() if count > 1
        ),
        "titles_on_both_sides": sum(1 for r in validation if r["title"].strip() in train_titles),
    }
    return train, validation, decided


def describe(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    relevant = sum(1 for r in rows if r[FIELD])
    dates = [_when(r["published_at"]) for r in rows]
    return {
        "total": len(rows),
        "relevant": relevant,
        "not_relevant": len(rows) - relevant,
        "positive_ratio": relevant / len(rows),
        "date_min": min(dates).isoformat().replace("+00:00", "Z"),
        "date_max": max(dates).isoformat().replace("+00:00", "Z"),
    }


def write_split(
    out_dir: Path,
    train: Sequence[Mapping[str, Any]],
    validation: Sequence[Mapping[str, Any]],
    decided: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    created_at: datetime,
    dataset: str = "court-monitor-relevance-v1-dev-split",
) -> dict[str, Any]:
    """Write train, validation and their metadata once; a second write of the same split
    changes nothing, of another is refused."""
    bodies = {
        name: "".join(json.dumps(dict(r), ensure_ascii=False) + "\n" for r in rows)
        for name, rows in (("train.jsonl", train), ("validation.jsonl", validation))
    }
    metadata_path = out_dir / "split_metadata.json"
    if metadata_path.exists() or any((out_dir / name).exists() for name in bodies):
        for name, body in bodies.items():
            path = out_dir / name
            if not path.exists() or path.read_text(encoding="utf-8") != body:
                raise FrozenError(f"{out_dir} holds another split; nothing changed")
        frozen: dict[str, Any] = json.loads(metadata_path.read_text(encoding="utf-8"))
        return frozen
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, body in bodies.items():
        write_atomically(out_dir / name, body)
    validation_stats = describe(validation)
    metadata = {
        "dataset": dataset,
        **decided,
        "train": describe(train),
        "validation": validation_stats,
        "validation_relevant_below_minimum": validation_stats["relevant"] < MIN_VALIDATION_RELEVANT,
        "balanced": False,
        "shuffled": False,
        "stratified": False,
        "train_sha256": hashlib.sha256((out_dir / "train.jsonl").read_bytes()).hexdigest(),
        "validation_sha256": hashlib.sha256(
            (out_dir / "validation.jsonl").read_bytes()
        ).hexdigest(),
        **source,
        "frozen": True,
        "created_at": created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
    write_atomically(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    return metadata

"""The migration of the development labels to `court-monitor-person-criminal-case-v2`: one
list of changes from two reviews, the second winning, and migrated labels frozen beside the
old ones with their provenance."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evaluation.relevance.court_relevance_migration import (
    LABELS,
    MIGRATION,
    MIGRATION_SOURCE,
    apply,
    merge,
    migrated,
    write_migration,
)
from evaluation.relevance.holdout import FrozenError
from evaluation.relevance.person_criminal_case_rubric import PROMPT_VERSION

FIELD = "relevant_to_court_monitor"
WHEN = datetime(2026, 10, 15, tzinfo=UTC)


def label(article_id: int, relevant: bool, source: str = "llm_consensus") -> dict[str, Any]:
    return {"article_id": article_id, FIELD: relevant, "annotation_source": source}


def v1(article_id: int, current: bool, proposed: bool, category: str = "c") -> dict[str, str]:
    return {
        "article_id": str(article_id),
        "title": f"t{article_id}",
        "current_label": str(current),
        "proposed_label": str(proposed),
        "category": category,
        "reason": "r1",
    }


def v2(article_id: int, current: bool, proposed: bool, group: str = "g") -> dict[str, str]:
    return {
        "article_id": str(article_id),
        "title": f"t{article_id}",
        "current_label": str(current),
        "proposed_label": str(proposed),
        "group": group,
        "reason": "r2",
    }


def articles(*ids: int, same: tuple[int, ...] = ()) -> list[dict[str, Any]]:
    return [{"article_id": i, "text_hash": "twin" if i in same else str(i)} for i in ids]


LABELS_ = [label(1, True), label(2, False), label(3, True), label(4, False), label(5, True)]


def test_v1_gives_only_its_true_to_false_and_v2_wins() -> None:
    changes, report = merge(
        LABELS_,
        [v1(1, True, False), v1(2, False, True), v1(3, True, False)],
        [v2(3, True, True), v2(4, False, True, "political_theme")],
        articles(1, 2, 3, 4, 5),
    )
    assert [(c.article_id, c.new_label, c.decided_by) for c in changes] == [
        (1, False, "v1"),
        (4, True, "v2"),
    ]
    assert report["v1_false_to_true_dropped"] == 1
    assert report["both_reviews"] == 1


def test_a_review_against_other_labels_is_refused() -> None:
    with pytest.raises(ValueError, match="another label"):
        merge(LABELS_, [v1(1, False, True)], [], articles(1, 2, 3, 4, 5))


def test_v2_makes_true_only_by_its_true_rules() -> None:
    with pytest.raises(ValueError, match="made true"):
        merge(LABELS_, [], [v2(2, False, True, "ordinary_crime")], articles(1, 2, 3, 4, 5))


def test_an_article_twice_in_a_review_is_refused() -> None:
    with pytest.raises(ValueError, match="twice"):
        merge(LABELS_, [v1(1, True, False), v1(1, True, False)], [], articles(1, 2, 3, 4, 5))


def test_a_v2_decision_holds_for_the_same_text() -> None:
    changes, report = merge(LABELS_, [], [v2(3, True, False)], articles(1, 2, 3, 4, 5, same=(3, 5)))
    assert [(c.article_id, c.new_label, c.decided_by) for c in changes] == [
        (3, False, "v2"),
        (5, False, "v2_duplicate"),
    ]
    assert report["v2_duplicates"] == [5]


def test_migrated_labels_keep_their_provenance(tmp_path: Path) -> None:
    changes, _ = merge(LABELS_, [v1(1, True, False)], [], articles(1, 2, 3, 4, 5))
    write_migration(tmp_path / MIGRATION, changes)
    from evaluation.relevance.court_relevance_migration import read_tsv

    rows = migrated(LABELS_, read_tsv(tmp_path / MIGRATION))
    assert rows[0] == {
        "article_id": 1,
        FIELD: False,
        "annotation_source": MIGRATION_SOURCE,
        "rubric_version": PROMPT_VERSION,
        "previous_label": True,
        "previous_annotation_source": "llm_consensus",
    }
    assert rows[1][FIELD] is False
    assert rows[1]["annotation_source"] == "llm_consensus"
    assert [r["article_id"] for r in rows] == [1, 2, 3, 4, 5]


def test_a_migration_that_changes_nothing_is_refused() -> None:
    row = {"article_id": "2", "previous_label": "False", "new_label": "False"}
    with pytest.raises(ValueError, match="changes nothing"):
        migrated(LABELS_, [row])


def _frozen_dev(tmp_path: Path) -> Path:
    body = "".join(json.dumps(r) + "\n" for r in LABELS_)
    (tmp_path / "final_labels.jsonl").write_text(body, encoding="utf-8")
    metadata = {
        "prompt_version": "court-monitor-relevance-v1",
        "articles_sha256": "a",
        "final_labels_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }
    (tmp_path / "final_dataset_metadata.json").write_text(json.dumps(metadata))
    changes, _ = merge(LABELS_, [v1(1, True, False)], [], articles(1, 2, 3, 4, 5))
    write_migration(tmp_path / MIGRATION, changes)
    return tmp_path


def test_apply_freezes_beside_the_old_labels(tmp_path: Path) -> None:
    dev = _frozen_dev(tmp_path)
    old = (dev / "final_labels.jsonl").read_text()
    metadata = apply(dev, created_at=WHEN)
    assert (dev / "final_labels.jsonl").read_text() == old
    assert metadata["true_to_false"] == 1
    assert metadata["relevant"] == 2
    assert apply(dev, created_at=WHEN) == metadata


def test_apply_refuses_another_build_over_a_frozen_one(tmp_path: Path) -> None:
    dev = _frozen_dev(tmp_path)
    apply(dev, created_at=WHEN)
    changes, _ = merge(LABELS_, [v1(3, True, False)], [], articles(1, 2, 3, 4, 5))
    write_migration(dev / MIGRATION, changes)
    with pytest.raises(FrozenError):
        apply(dev, created_at=WHEN)
    assert (dev / LABELS).exists()


def test_apply_refuses_changed_old_labels(tmp_path: Path) -> None:
    dev = _frozen_dev(tmp_path)
    (dev / "final_labels.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="changed"):
        apply(dev, created_at=WHEN)

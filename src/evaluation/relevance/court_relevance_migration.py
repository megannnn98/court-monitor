"""The development labels moved to `court-monitor-person-criminal-case-v2`.

    python -m evaluation.relevance.court_relevance_migration merge
    python -m evaluation.relevance.court_relevance_migration apply
    python -m evaluation.relevance.court_relevance_migration split

`merge` joins the two reviews of the frozen development labels into one list of changes,
`rubric_migration_v2.tsv`: of the first review (v1) only its true -> false decisions —
its false -> true ones took any criminal case and are dropped — and over them every
decision of the second review (v2), which wins wherever both speak. A review written
against other labels than the frozen ones is refused, and so is a false -> true that the
second review did not make on one of its own true rules. A decision of the second review
holds for every article of the same normalized text too (`v2_duplicate`), so that equal
texts never keep different labels.

`apply` writes the migrated labels beside the frozen ones, never over them:
`final_labels_v2.jsonl` and its metadata, each label with its previous label and source;
a changed one has the source `rubric_migration_v2`. `split` cuts them by time, by the
rules of `court_relevance_split`, into `court_monitor_v2/`.

No model is called and the frozen holdout is never read.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.relevance.holdout import FrozenError, read_jsonl, write_atomically
from evaluation.relevance.person_criminal_case_rubric import PROMPT_VERSION

ROOT = Path(__file__).resolve().parents[3]
DEV_DIR = ROOT / "data/ml/relevance/court_monitor_dev"
SPLIT_DIR = ROOT / "data/ml/relevance/court_monitor_v2"
FIELD = "relevant_to_court_monitor"
MIGRATION_SOURCE = "rubric_migration_v2"
REVIEW_V1 = "review_person_criminal_case_v1.tsv"
REVIEW_V2 = "review_person_criminal_case_v2.tsv"
MIGRATION = "rubric_migration_v2.tsv"
LABELS = "final_labels_v2.jsonl"
METADATA = "final_dataset_metadata_v2.json"
# The only rules of v2 that make an article relevant; ordinary crime is never among them.
V2_TRUE_GROUPS = frozenset({"arson_sabotage_no_victims", "political_theme"})
COLUMNS = (
    "article_id",
    "title",
    "previous_label",
    "previous_annotation_source",
    "new_label",
    "decided_by",
    "category",
    "reason",
)


@dataclass(frozen=True)
class Change:
    article_id: int
    title: str
    previous_label: bool
    previous_annotation_source: str
    new_label: bool
    decided_by: str
    category: str
    reason: str


def _flag(value: str) -> bool:
    if value not in ("True", "False"):
        raise ValueError(f"not a label: {value!r}")
    return value == "True"


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def merge(
    labels: Sequence[Mapping[str, Any]],
    review_v1: Sequence[Mapping[str, str]],
    review_v2: Sequence[Mapping[str, str]],
    articles: Sequence[Mapping[str, Any]],
) -> tuple[list[Change], dict[str, Any]]:
    """The changes, one per article, and what was taken, dropped and overridden."""
    held = {int(row["article_id"]): row for row in labels}
    text_of = {int(a["article_id"]): str(a["text_hash"]) for a in articles}
    decided: dict[int, Change] = {}
    dropped_v1_true = []
    for name, review in (("v1", review_v1), ("v2", review_v2)):
        seen: set[int] = set()
        for row in review:
            article_id = int(row["article_id"])
            if article_id in seen:
                raise ValueError(f"{name}: {article_id} twice")
            seen.add(article_id)
            label = held.get(article_id)
            if label is None:
                raise ValueError(f"{name}: {article_id} is not a development article")
            if _flag(row["current_label"]) != label[FIELD]:
                raise ValueError(f"{name}: {article_id} was reviewed against another label")
            proposed = _flag(row["proposed_label"])
            category = row["category"] if name == "v1" else row["group"]
            if name == "v1" and proposed:
                dropped_v1_true.append(article_id)
                continue
            if name == "v2" and proposed and not label[FIELD] and category not in V2_TRUE_GROUPS:
                raise ValueError(f"v2: {article_id} made true by {category!r}")
            decided[article_id] = Change(
                article_id=article_id,
                title=row["title"],
                previous_label=bool(label[FIELD]),
                previous_annotation_source=str(label["annotation_source"]),
                new_label=proposed,
                decided_by=name,
                category=category,
                reason=row["reason"],
            )
    v2_ids = {int(row["article_id"]) for row in review_v2}
    v1_ids = {int(row["article_id"]) for row in review_v1}
    by_text: dict[str, list[int]] = {}
    for article_id, digest in sorted(text_of.items()):
        by_text.setdefault(digest, []).append(article_id)
    twins = []
    for article_id in sorted(v2_ids):
        source = decided[article_id]
        for twin in by_text[text_of[article_id]]:
            if twin in v2_ids:
                if decided[twin].new_label != source.new_label:
                    raise ValueError(f"v2 decides {article_id} and its twin {twin} apart")
                continue
            twins.append(twin)
            decided[twin] = Change(
                article_id=twin,
                title=source.title,
                previous_label=bool(held[twin][FIELD]),
                previous_annotation_source=str(held[twin]["annotation_source"]),
                new_label=source.new_label,
                decided_by="v2_duplicate",
                category=source.category,
                reason=f"тот же нормализованный текст, что {article_id}: {source.reason}",
            )
    changes = [
        change for _, change in sorted(decided.items()) if change.new_label != change.previous_label
    ]
    if any(c.new_label and not c.decided_by.startswith("v2") for c in changes):
        raise AssertionError("a false -> true not decided by v2")
    report = {
        "v1_rows": len(review_v1),
        "v2_rows": len(review_v2),
        "v1_false_to_true_dropped": len(dropped_v1_true),
        "both_reviews": len(v1_ids & v2_ids),
        "v2_duplicates": sorted(twins),
        "decided_by": dict(Counter(c.decided_by for c in changes)),
        "true_to_false": sum(1 for c in changes if not c.new_label),
        "false_to_true": sum(1 for c in changes if c.new_label),
    }
    return changes, report


def write_migration(path: Path, changes: Sequence[Change]) -> None:
    rows = [
        {
            "article_id": c.article_id,
            "title": c.title,
            "previous_label": c.previous_label,
            "previous_annotation_source": c.previous_annotation_source,
            "new_label": c.new_label,
            "decided_by": c.decided_by,
            "category": c.category,
            "reason": c.reason,
        }
        for c in changes
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def migrated(
    labels: Sequence[Mapping[str, Any]], migration: Sequence[Mapping[str, str]]
) -> list[dict[str, Any]]:
    """Every label, in the frozen order, under the new rubric with its provenance."""
    by_id = {int(row["article_id"]): row for row in migration}
    if len(by_id) != len(migration):
        raise ValueError("the migration holds an article twice")
    held = {int(row["article_id"]) for row in labels}
    stray = set(by_id) - held
    if stray:
        raise ValueError(f"migration of unknown articles: {sorted(stray)[:5]}")
    result = []
    for row in labels:
        article_id = int(row["article_id"])
        previous, source = bool(row[FIELD]), str(row["annotation_source"])
        change = by_id.get(article_id)
        if change is not None:
            if _flag(change["previous_label"]) != previous:
                raise ValueError(f"{article_id}: the migration expects another label")
            if _flag(change["new_label"]) == previous:
                raise ValueError(f"{article_id}: a migration that changes nothing")
        result.append(
            {
                "article_id": article_id,
                FIELD: previous if change is None else _flag(change["new_label"]),
                "annotation_source": source if change is None else MIGRATION_SOURCE,
                "rubric_version": PROMPT_VERSION,
                "previous_label": previous,
                "previous_annotation_source": source,
            }
        )
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_labels(dev_dir: Path) -> list[dict[str, Any]]:
    """The frozen labels of `court-monitor-relevance-v1`, once checked to be unchanged."""
    frozen = json.loads((dev_dir / "final_dataset_metadata.json").read_text(encoding="utf-8"))
    if _sha256(dev_dir / "final_labels.jsonl") != frozen["final_labels_sha256"]:
        raise ValueError("final_labels.jsonl changed since it was frozen")
    return read_jsonl(dev_dir / "final_labels.jsonl")


def apply(dev_dir: Path, *, created_at: datetime) -> dict[str, Any]:
    """Freeze the migrated labels and their metadata; a second build of the same labels
    changes nothing, of others is refused."""
    labels = checked_labels(dev_dir)
    rows = migrated(labels, read_tsv(dev_dir / MIGRATION))
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    labels_path, metadata_path = dev_dir / LABELS, dev_dir / METADATA
    if labels_path.exists() or metadata_path.exists():
        if not (labels_path.exists() and metadata_path.exists()):
            raise FrozenError("half a frozen set: one of the migrated files is missing")
        if labels_path.read_text(encoding="utf-8") != body:
            raise FrozenError(f"{LABELS} is frozen and a build now differs; nothing changed")
        frozen: dict[str, Any] = json.loads(metadata_path.read_text(encoding="utf-8"))
        return frozen
    write_atomically(labels_path, body)
    relevant = sum(1 for row in rows if row[FIELD])
    changed = [row for row in rows if row["annotation_source"] == MIGRATION_SOURCE]
    previous = json.loads((dev_dir / "final_dataset_metadata.json").read_text(encoding="utf-8"))
    metadata = {
        "dataset": "court-monitor-person-criminal-case-v2-dev",
        "rubric_version": PROMPT_VERSION,
        "previous_rubric_version": previous["prompt_version"],
        "article_count": len(rows),
        "relevant": relevant,
        "not_relevant": len(rows) - relevant,
        "true_to_false": sum(1 for row in changed if not row[FIELD]),
        "false_to_true": sum(1 for row in changed if row[FIELD]),
        "sources": dict(sorted(Counter(row["annotation_source"] for row in rows).items())),
        "articles_sha256": previous["articles_sha256"],
        "previous_final_labels_sha256": previous["final_labels_sha256"],
        "migration_sha256": _sha256(dev_dir / MIGRATION),
        "final_labels_sha256": _sha256(labels_path),
        "frozen": True,
        "created_at": created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
    write_atomically(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    return metadata


def _merge() -> int:
    from evaluation.relevance.court_relevance_dev import checked_articles

    articles, _ = checked_articles(DEV_DIR)
    labels = checked_labels(DEV_DIR)
    changes, report = merge(
        labels, read_tsv(DEV_DIR / REVIEW_V1), read_tsv(DEV_DIR / REVIEW_V2), articles
    )
    path = DEV_DIR / MIGRATION
    if path.exists():
        print(f"refused: {path} exists; nothing changed", file=sys.stderr)
        return 1
    write_migration(path, changes)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _split() -> int:
    from evaluation.relevance.court_relevance_dev import checked_articles
    from evaluation.relevance.court_relevance_split import labelled, split, write_split

    articles, digest = checked_articles(DEV_DIR)
    frozen = json.loads((DEV_DIR / METADATA).read_text(encoding="utf-8"))
    if _sha256(DEV_DIR / LABELS) != frozen["final_labels_sha256"]:
        print(f"refused: {LABELS} changed since it was frozen", file=sys.stderr)
        return 1
    train, validation, decided = split(labelled(articles, read_jsonl(DEV_DIR / LABELS)))
    try:
        metadata = write_split(
            SPLIT_DIR,
            train,
            validation,
            decided,
            {"articles_sha256": digest, "final_labels_sha256": frozen["final_labels_sha256"]},
            created_at=datetime.now(UTC),
            dataset="court-monitor-person-criminal-case-v2-dev-split",
        )
    except FrozenError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["merge"]:
        return _merge()
    if args == ["apply"]:
        try:
            metadata = apply(DEV_DIR, created_at=datetime.now(UTC))
        except (FrozenError, ValueError) as error:
            print(f"refused: {error}", file=sys.stderr)
            return 1
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return 0
    if args == ["split"]:
        return _split()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

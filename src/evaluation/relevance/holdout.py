"""The relevance holdout: a stream of articles no model has seen, labelled by hand.

Every article of the window is taken, in publication order — no sampling, no stratum, no
model. The only articles left out are those whose text the earlier data already holds:
the same text once normalized (NFC, whitespace, case), counted apart from texts that are
identical byte for byte. Equal titles are reported, never removed.

Nothing here knows of a classifier: no probabilities, no thresholds, no preprocessing of
the baseline. Once written, the holdout is frozen: a second build refuses and changes
nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATASET = "relevance-holdout-v1"
RUBRIC = "junk-labels-v1"
START_AT = datetime(2026, 9, 17, tzinfo=UTC)
# The kinds of `junk-labels-v1` (`evaluation.junk_screen.labels.KINDS`), the same meaning.
KINDS = ("case", "administrative", "past_only", "foreign", "crime_no_case", "other")
FILES = ("articles.jsonl", "metadata.json", "leakage_report.json")


class FrozenError(RuntimeError):
    """The holdout exists already; it is never rebuilt or merged."""


def normalize(text: str) -> str:
    """A text as compared for duplicates: NFC, whitespace collapsed, case folded."""
    return " ".join(unicodedata.normalize("NFC", text).split()).casefold()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def _when(value: datetime | str) -> datetime:
    when = datetime.fromisoformat(value) if isinstance(value, str) else value
    if when.tzinfo is None:
        raise ValueError(f"published_at without a time zone: {value!r}")
    return when.astimezone(UTC)


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat().replace("+00:00", "Z")


def ordered(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rows in publication order, ties by article id: the same order every time."""
    return sorted(
        (dict(row) for row in rows),
        key=lambda row: (_when(row["published_at"]), int(row["article_id"])),
    )


def _matches(
    new: Sequence[Mapping[str, Any]],
    old: Sequence[Mapping[str, Any]],
    key: str,
    of: Any,
) -> list[dict[str, Any]]:
    index: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in old:
        value = of(row)
        if value not in (None, ""):
            index[value].append({"article_id": row["article_id"], "file": row["file"]})
    found = []
    for row in new:
        value = of(row)
        if value in index:
            found.append({key: row["article_id"], "old": index[value]})
    return found


def leakage(
    new: Sequence[Mapping[str, Any]], old: Sequence[Mapping[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """What new articles share with the earlier data, by kind of match. `old` rows carry
    the file they came from."""
    return {
        "article_id_duplicates": _matches(new, old, "article_id", lambda r: r["article_id"]),
        "exact_text_duplicates": _matches(new, old, "article_id", lambda r: r["text"]),
        "normalized_text_hash_duplicates": _matches(
            new, old, "article_id", lambda r: text_hash(r["text"])
        ),
        "title_duplicates": _matches(new, old, "article_id", lambda r: r["title"].strip()),
    }


def build(
    rows: Iterable[Mapping[str, Any]],
    old: Sequence[Mapping[str, Any]],
    *,
    start_at: datetime = START_AT,
    created_at: datetime,
    git_commit: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """The holdout's articles, its metadata and its leakage report.

    Of `rows`, every article published from `start_at` on with a text goes in, except a
    text the earlier data already holds once normalized. The window ends at the newest
    article taken."""
    candidates = [row for row in ordered(rows) if _when(row["published_at"]) >= start_at]
    without_text = [row for row in candidates if not (row.get("text") or "").strip()]
    in_window = [row for row in candidates if (row.get("text") or "").strip()]
    report: dict[str, Any] = dict(leakage(in_window, old))
    excluded = {match["article_id"] for match in report["normalized_text_hash_duplicates"]}
    exact = {match["article_id"] for match in report["exact_text_duplicates"]}
    articles = [
        {
            "article_id": int(row["article_id"]),
            "title": row["title"],
            "text": row["text"],
            "source": row["source"],
            "url": row["url"],
            "published_at": _iso(_when(row["published_at"])),
            "text_hash": text_hash(row["text"]),
        }
        for row in in_window
        if row["article_id"] not in excluded
    ]
    if not articles:
        raise ValueError("no articles in the window")
    hashes = defaultdict(list)
    for article in articles:
        hashes[article["text_hash"]].append(article["article_id"])
    within = [ids for ids in hashes.values() if len(ids) > 1]
    report["within_holdout_normalized_duplicates"] = [{"article_ids": ids} for ids in within]
    report["counts"] = {
        "article_id_duplicates": len(report["article_id_duplicates"]),
        "exact_text_duplicates": len(report["exact_text_duplicates"]),
        "normalized_text_hash_duplicates": len(report["normalized_text_hash_duplicates"]),
        "title_duplicates": len(report["title_duplicates"]),
        "within_holdout_normalized_duplicate_groups": len(within),
    }
    published = [_when(article["published_at"]) for article in articles]
    metadata = {
        "dataset": DATASET,
        "start_at": _iso(start_at),
        "end_at": _iso(max(published)),
        "first_published_at": _iso(min(published)),
        "created_at": _iso(created_at),
        "rubric": RUBRIC,
        "kinds": list(KINDS),
        "selection": "consecutive temporal stream",
        "model_predictions_used_for_selection": False,
        "model_predictions_used_for_annotation": False,
        "git_commit": git_commit,
        "frozen": True,
        "article_count": len(articles),
        "found_in_window": len(in_window),
        "excluded_normalized_text_hash_duplicates": len(excluded),
        "of_them_exact_text_duplicates": len(exact & excluded),
        "of_them_normalized_only": len(excluded - exact),
        "title_duplicates_reported": len(report["title_duplicates"]),
        "articles_without_text": len(without_text),
        "old_data": sorted({row["file"] for row in old}),
    }
    return articles, metadata, report


def write_frozen(
    out_dir: Path,
    articles: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Any],
    report: Mapping[str, Any],
) -> None:
    """Write the three files of a new holdout; refuse, changing nothing, if any exists."""
    existing = [name for name in FILES if (out_dir / name).exists()]
    if existing:
        raise FrozenError(f"{out_dir} is frozen: {', '.join(existing)} already exist")
    out_dir.mkdir(parents=True, exist_ok=True)
    contents = {
        "articles.jsonl": "".join(
            json.dumps(article, ensure_ascii=False) + "\n" for article in articles
        ),
        "metadata.json": json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        "leakage_report.json": json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    }
    staged = {
        name: write_atomically(out_dir / f".{name}.staged", body) for name, body in contents.items()
    }
    for name, path in staged.items():
        os.replace(path, out_dir / name)


def write_atomically(path: Path, body: str) -> Path:
    """`body` in `path`, whole or not at all: a temporary file, flushed, then renamed."""
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(body)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]

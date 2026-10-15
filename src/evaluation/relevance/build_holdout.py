"""Build the relevance holdout from the database, once.

    HOLDOUT_DATABASE_URL=postgresql+psycopg://… python -m evaluation.relevance.build_holdout

Reads, in a read-only transaction, every article published from 2026-09-17 on; compares
it with the earlier data (the relevance train and test, the junk screen's sample); writes
`data/ml/relevance/holdout/`. A holdout already there is never touched.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from evaluation.relevance.holdout import (
    FILES,
    START_AT,
    FrozenError,
    build,
    read_jsonl,
    write_frozen,
)

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "data/ml/relevance/holdout"
OLD_DATA = (
    ROOT / "data/ml/relevance/train.jsonl",
    ROOT / "data/ml/relevance/test.jsonl",
    ROOT / "var/junk_screen/sample.jsonl",
)

_ARTICLES = text(
    """
    SELECT a.id AS article_id, a.title, a.text, a.published_at, s.name AS source,
           d.canonical_url AS url
    FROM parsed_articles a
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE a.published_at >= :start_at
    ORDER BY a.published_at, a.id
    """
)


def read_articles(database_url: str, start_at: datetime) -> list[dict[str, Any]]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            rows = connection.execute(_ARTICLES, {"start_at": start_at}).mappings().all()
            connection.rollback()
    finally:
        engine.dispose()
    return [dict(row) for row in rows]


def read_old(paths: Sequence[Path]) -> list[dict[str, Any]]:
    old: list[dict[str, Any]] = []
    for path in paths:
        for row in read_jsonl(path):
            old.append(
                {
                    "article_id": row["article_id"],
                    "title": row["title"],
                    "text": row["text"],
                    "file": str(path.relative_to(ROOT)),
                }
            )
    return old


def git_commit() -> str | None:
    try:
        head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"{head}+dirty" if dirty else head


def main() -> int:
    database_url = os.environ.get("HOLDOUT_DATABASE_URL", "").strip()
    if not database_url:
        print("HOLDOUT_DATABASE_URL is not set", file=sys.stderr)
        return 2
    if any((OUT_DIR / name).exists() for name in FILES):
        print(f"{OUT_DIR} is frozen; nothing changed", file=sys.stderr)
        return 1
    articles, metadata, report = build(
        read_articles(database_url, START_AT),
        read_old(OLD_DATA),
        created_at=datetime.now(UTC),
        git_commit=git_commit(),
    )
    try:
        write_frozen(OUT_DIR, articles, metadata, report)
    except FrozenError as error:
        print(f"{error}; nothing changed", file=sys.stderr)
        return 1
    for key in (
        "start_at",
        "end_at",
        "first_published_at",
        "found_in_window",
        "article_count",
        "excluded_normalized_text_hash_duplicates",
        "of_them_exact_text_duplicates",
        "of_them_normalized_only",
        "title_duplicates_reported",
        "articles_without_text",
        "git_commit",
    ):
        print(f"{key}: {metadata[key]}")
    print(f"leakage: {report['counts']}")
    print(f"written: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

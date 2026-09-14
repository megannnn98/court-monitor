"""Migration l6m7n8o9p0q1 on a database that already holds duplicate pending reviews."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "k5l6m7n8o9p0"


def _alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": database_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_duplicate_pending_reviews_stop_migration_with_actionable_message(
    session_factory: sessionmaker[Session],
) -> None:
    engine = session_factory.kw["bind"]
    database_url = engine.url.render_as_string(hide_password=False)

    downgraded = _alembic(database_url, "downgrade", PREVIOUS_REVISION)
    assert downgraded.returncode == 0, downgraded.stderr
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO review_records (subject_type, subject_id, decision) VALUES "
                    "('rosfinmatch', 5, 'pending'), ('rosfinmatch', 5, 'pending'), "
                    "('rosfinmatch', 6, 'pending'), ('rosfinmatch', 6, 'rejected')"
                )
            )

        upgraded = _alembic(database_url, "upgrade", "head")

        assert upgraded.returncode != 0
        assert "Cannot create uq_review_records_pending_subject" in upgraded.stderr
        assert "1 subject(s) have more than one pending review" in upgraded.stderr
        assert "rosfinmatch/5" in upgraded.stderr
        assert "UniqueViolation" not in upgraded.stderr
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM review_records"))
        restored = _alembic(database_url, "upgrade", "head")
        assert restored.returncode == 0, restored.stderr

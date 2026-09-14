"""Migration o9p0q1r2s3t4: matching_key stops being an identity key."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "n8o9p0q1r2s3"


def _alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _indexes(connection) -> dict[str, str]:  # type: ignore[no-untyped-def]
    rows = connection.execute(
        text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'persons'")
    )
    return {name: definition for name, definition in rows}


def test_upgrade_keeps_existing_persons_and_allows_active_namesakes(
    session_factory: sessionmaker[Session],
) -> None:
    engine = session_factory.kw["bind"]
    database_url = engine.url.render_as_string(hide_password=False)
    insert = text(
        "INSERT INTO persons (canonical_name, normalized_name, matching_key, status) "
        "VALUES ('Алексей Сергеевич Иванов', 'Алексей Сергеевич Иванов', 'асиванов', 'active')"
    )

    downgraded = _alembic(database_url, "downgrade", PREVIOUS_REVISION)
    assert downgraded.returncode == 0, downgraded.stderr
    try:
        with engine.begin() as connection:
            connection.execute(insert)
            before = connection.execute(text("SELECT id, matching_key, status FROM persons")).all()

        upgraded = _alembic(database_url, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr

        with engine.begin() as connection:
            assert (
                connection.execute(text("SELECT id, matching_key, status FROM persons")).all()
                == before
            )
            indexes = _indexes(connection)
            connection.execute(insert)  # a second active namesake
        assert "uq_persons_matching_key_active" not in indexes
        assert "UNIQUE" not in indexes["ix_persons_matching_key_active"]
        assert "WHERE" in indexes["ix_persons_matching_key_active"]

        blocked = _alembic(database_url, "downgrade", PREVIOUS_REVISION)
        assert blocked.returncode != 0
        assert "Cannot restore uq_persons_matching_key_active" in blocked.stderr
        assert "'асиванов' x2" in blocked.stderr
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM persons"))
        restored = _alembic(database_url, "upgrade", "head")
        assert restored.returncode == 0, restored.stderr

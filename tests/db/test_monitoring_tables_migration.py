"""Migration p0q1r2s3t4u5: monitoring tables are additive and reversible."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "o9p0q1r2s3t4"
MONITORING_TABLES = {
    "monitoring_runs",
    "monitoring_run_items",
    "source_monitoring_state",
    "monitoring_findings",
}


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


def test_downgrade_drops_monitoring_tables_and_keeps_domain_rows(
    session_factory: sessionmaker[Session],
) -> None:
    engine = session_factory.kw["bind"]
    database_url = engine.url.render_as_string(hide_password=False)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO persons (canonical_name, normalized_name, matching_key, status) "
                "VALUES ('Сергей Сидоров', 'Сергей Сидоров', 'ссидоров', 'active')"
            )
        )
        before = connection.execute(text("SELECT id, matching_key FROM persons")).all()

    downgraded = _alembic(database_url, "downgrade", PREVIOUS_REVISION)
    assert downgraded.returncode == 0, downgraded.stderr
    try:
        assert MONITORING_TABLES.isdisjoint(inspect(engine).get_table_names())
        with engine.begin() as connection:
            assert connection.execute(text("SELECT id, matching_key FROM persons")).all() == before
    finally:
        upgraded = _alembic(database_url, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr

    assert MONITORING_TABLES <= set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        assert connection.execute(text("SELECT id, matching_key FROM persons")).all() == before

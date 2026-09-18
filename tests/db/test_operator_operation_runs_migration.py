"""Migration s3t4u5v6w7x8: operator_operation_runs is additive and reversible, and the
database itself admits one live run per operation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "r2s3t4u5v6w7"
INSERT = (
    "INSERT INTO operator_operation_runs (operation_name, parameters, command, status) "
    "VALUES (:name, '{}', '[]', :status)"
)


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


def test_downgrade_drops_the_table_and_upgrade_restores_it(
    session_factory: sessionmaker[Session],
) -> None:
    engine = session_factory.kw["bind"]
    database_url = engine.url.render_as_string(hide_password=False)

    downgraded = _alembic(database_url, "downgrade", PREVIOUS_REVISION)
    assert downgraded.returncode == 0, downgraded.stderr
    try:
        assert "operator_operation_runs" not in inspect(engine).get_table_names()
    finally:
        upgraded = _alembic(database_url, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr

    assert "operator_operation_runs" in inspect(engine).get_table_names()


def test_the_database_admits_one_live_run_per_operation(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        session.execute(text(INSERT), {"name": "extract-entities", "status": "running"})
        # Finished runs and other operations do not count.
        session.execute(text(INSERT), {"name": "extract-entities", "status": "failed"})
        session.execute(text(INSERT), {"name": "resolve-people", "status": "pending"})

    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.execute(text(INSERT), {"name": "extract-entities", "status": "pending"})


def test_an_unknown_status_is_refused(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.execute(text(INSERT), {"name": "extract-entities", "status": "paused"})

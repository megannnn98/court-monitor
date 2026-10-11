"""The migration tests leave the shared test database alone (`migration_session_factory`)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]


def _revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(connection.scalar(text("SELECT version_num FROM alembic_version")))


def test_a_downgrade_here_does_not_reach_the_shared_database(
    migration_session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    engine = migration_session_factory.kw["bind"]
    head = _revision(test_engine)
    assert engine.url.database != test_engine.url.database
    assert _revision(engine) == head

    downgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "-1"],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "DATABASE_URL": engine.url.render_as_string(hide_password=False),
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert downgraded.returncode == 0, downgraded.stderr
    assert _revision(engine) != head
    assert _revision(test_engine) == head


def test_no_test_that_downgrades_takes_the_shared_database() -> None:
    tests = REPO_ROOT / "tests"
    offenders = [
        str(path.relative_to(tests))
        for path in sorted(tests.rglob("test_*.py"))
        if path != Path(__file__)
        and '"downgrade"' in (source := path.read_text(encoding="utf-8"))
        and re.search(r"(?<!migration_)\bsession_factory\b", source)
    ]

    assert offenders == []

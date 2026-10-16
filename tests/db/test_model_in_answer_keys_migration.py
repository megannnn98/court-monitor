"""Migration o6p7q8r9s0t1: the model joins the key of the cached answers.

Upgraded, two models' answers to one question live side by side; downgraded, the newest
of them is kept and the old key is back."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
REVISION = "o6p7q8r9s0t1"
PREVIOUS_REVISION = "n5o6p7q8r9s0"
TABLES = (
    "entity_role_answers",
    "entity_politics_answers",
    "entity_news_answers",
    "unnamed_answers",
    "entity_name_normalizations",
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


def _key_of(session: Session, table: str) -> str:
    return str(
        session.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = :name AND contype = 'p'"
            ),
            {"name": f"{table}_pkey"},
        )
    )


def test_two_models_answers_survive_the_upgrade_and_the_newest_survives_the_downgrade(
    migration_session_factory: sessionmaker[Session],
) -> None:
    engine = migration_session_factory.kw["bind"]
    database_url = engine.url.render_as_string(hide_password=False)
    assert _alembic(database_url, "downgrade", PREVIOUS_REVISION).returncode == 0
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entity_role_answers (key, input_hash, prompt_version, model, kind, "
                "explanation, created_at) VALUES ('михаил звинчук', 'h', 'roles-v5', 'free', "
                "'accused', 'старый', now() - interval '1 hour')"
            )
        )

    upgraded = _alembic(database_url, "upgrade", REVISION)
    assert upgraded.returncode == 0, upgraded.stderr
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entity_role_answers (key, input_hash, prompt_version, model, kind, "
                "explanation) VALUES ('михаил звинчук', 'h', 'roles-v5', 'deepseek', "
                "'foreign', 'новый')"
            )
        )
    with migration_session_factory() as session:
        for table in TABLES:
            assert "model" in _key_of(session, table), table
        assert session.scalar(text("SELECT count(*) FROM entity_role_answers")) == 2

    downgraded = _alembic(database_url, "downgrade", PREVIOUS_REVISION)
    assert downgraded.returncode == 0, downgraded.stderr
    with migration_session_factory() as session:
        for table in TABLES:
            assert "model" not in _key_of(session, table), table
        rows = session.execute(text("SELECT model, kind FROM entity_role_answers")).all()
    assert [tuple(row) for row in rows] == [("deepseek", "foreign")]

    restored = _alembic(database_url, "upgrade", "head")
    assert restored.returncode == 0, restored.stderr

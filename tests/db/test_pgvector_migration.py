"""Migrations t4u5v6w7x8y9 and u5v6w7x8y9z0: pgvector is additive and reversible, keeps
pg_trgm, records that the `indexed_at` marks existing before it were set by Qdrant
rebuilds, and stores vectors PLAIN."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "s3t4u5v6w7x8"
TABLES = {"semantic_vector_collections", "semantic_vectors", "semantic_index_state"}


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


def _extensions(session_factory: sessionmaker[Session]) -> set[str]:
    with session_factory() as session:
        return set(session.execute(text("SELECT extname FROM pg_extension")).scalars())


def test_upgrade_adds_pgvector_and_attributes_existing_marks_to_qdrant(
    session_factory: sessionmaker[Session],
) -> None:
    engine = session_factory.kw["bind"]
    database_url = engine.url.render_as_string(hide_password=False)

    downgraded = _alembic(database_url, "downgrade", PREVIOUS_REVISION)
    try:
        assert downgraded.returncode == 0, downgraded.stderr
        assert not TABLES & set(inspect(engine).get_table_names())
        assert "vector" not in _extensions(session_factory)
        with session_factory.begin() as session:
            session.execute(
                text(
                    "INSERT INTO semantic_documents (entity_type, entity_id, "
                    "representation_version, content_hash, text, indexed_at) VALUES "
                    "('person', 1, 1, 'h1', 'суд', now()), "
                    "('event', 2, 1, 'h2', 'обыск', NULL)"
                )
            )
    finally:
        upgraded = _alembic(database_url, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr

    assert TABLES <= set(inspect(engine).get_table_names())
    assert {"vector", "pg_trgm"} <= _extensions(session_factory)
    with session_factory() as session:
        rows = session.execute(
            text("SELECT entity_type, vector_backend FROM semantic_index_state")
        ).all()
    state = {str(row[0]): str(row[1]) for row in rows}
    # Only the indexed person: the event has no marks to protect.
    assert state == {"person": "qdrant"}


def _embedding_storage(session_factory: sessionmaker[Session]) -> str:
    with session_factory() as session:
        return str(
            session.execute(
                text(
                    "SELECT attstorage FROM pg_attribute WHERE attrelid = "
                    "'semantic_vectors'::regclass AND attname = 'embedding'"
                )
            ).scalar_one()
        )


def test_plain_storage_migration_is_reversible(session_factory: sessionmaker[Session]) -> None:
    """u5v6w7x8y9z0 sets PLAIN; its downgrade restores the `vector` type's EXTERNAL."""
    engine = session_factory.kw["bind"]
    database_url = engine.url.render_as_string(hide_password=False)

    downgraded = _alembic(database_url, "downgrade", "t4u5v6w7x8y9")
    try:
        assert downgraded.returncode == 0, downgraded.stderr
        assert _embedding_storage(session_factory) == "e"
    finally:
        upgraded = _alembic(database_url, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr

    assert _embedding_storage(session_factory) == "p"


def test_downgrade_keeps_the_vector_extension_another_table_uses(
    session_factory: sessionmaker[Session],
) -> None:
    """The migration may drop `vector` only when nothing else in the database uses it."""
    engine = session_factory.kw["bind"]
    database_url = engine.url.render_as_string(hide_password=False)
    with session_factory.begin() as session:
        session.execute(text("CREATE TABLE other_vectors (embedding vector(3))"))
    try:
        downgraded = _alembic(database_url, "downgrade", PREVIOUS_REVISION)
        assert downgraded.returncode == 0, downgraded.stderr
        assert "vector" in _extensions(session_factory)
    finally:
        with session_factory.begin() as session:
            session.execute(text("DROP TABLE IF EXISTS other_vectors"))
        upgraded = _alembic(database_url, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr

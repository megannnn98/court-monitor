"""The removed semantic subsystem leaves nothing in the database (ADR 0028): no table of
it, no pgvector — on a new database and on one that still has them."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
GONE = (
    "publication_vectors",
    "semantic_documents",
    "semantic_vector_collections",
    "semantic_vectors",
    "semantic_index_state",
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


def _url(factory: sessionmaker[Session]) -> str:
    return str(factory.kw["bind"].url.render_as_string(hide_password=False))


def _tables(session: Session) -> set[str]:
    return set(session.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")))


def _has_extension(session: Session) -> bool:
    return bool(session.scalar(text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")))


def test_a_new_database_has_no_table_of_the_semantic_subsystem_and_no_pgvector(
    migration_session_factory: sessionmaker[Session],
) -> None:
    """The fixture migrated a new database to head. It holds the investigation's runs
    and nothing of the vectors; the migration did not ask for the extension, so it runs
    on a server that does not have pgvector installed at all."""
    with migration_session_factory() as session:
        tables = _tables(session)
        assert "investigation_runs" in tables
        assert not tables & set(GONE)
        assert not _has_extension(session)
        # No column of any table is of a pgvector type.
        assert not session.scalar(
            text(
                "SELECT count(*) FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid "
                "WHERE t.typname IN ('vector', 'halfvec', 'sparsevec') AND a.attnum > 0"
            )
        )


def _needs_pgvector(session_factory_of_the_test: sessionmaker[Session]) -> None:
    with session_factory_of_the_test() as session:
        available = session.scalar(
            text("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'")
        )
    if not available:
        pytest.skip("this server has no pgvector to put a database in the old state with")


def _old_state(factory: sessionmaker[Session], revision: str, *statements: str) -> str:
    """The database stepped back to `revision` with the extension and whatever the
    statements make, as a database migrated before the removal has it."""
    url = _url(factory)
    assert _alembic(url, "downgrade", revision).returncode == 0
    with factory.begin() as session:
        session.execute(text("CREATE EXTENSION vector"))
        for statement in statements:
            session.execute(text(statement))
    return url


OLD_TABLES = (
    "CREATE TABLE semantic_documents (id integer PRIMARY KEY)",
    "CREATE TABLE semantic_vector_collections (name varchar(64) PRIMARY KEY)",
    (
        "CREATE TABLE semantic_vectors (collection_name varchar(64) REFERENCES "
        "semantic_vector_collections(name), embedding vector NOT NULL)"
    ),
    "CREATE TABLE semantic_index_state (entity_type varchar(32))",
    "INSERT INTO semantic_documents VALUES (1)",
)


def test_a_database_that_still_has_the_tables_and_the_extension_loses_them(
    migration_session_factory: sessionmaker[Session],
) -> None:
    """A database migrated before the removal: the tables as the old revisions made them,
    with a vector column, and the extension this role installed."""
    _needs_pgvector(migration_session_factory)
    url = _old_state(migration_session_factory, "l2m3n4o5p6q7", *OLD_TABLES)

    upgraded = _alembic(url, "upgrade", "head")

    assert upgraded.returncode == 0, upgraded.stderr
    with migration_session_factory() as session:
        assert not _tables(session) & set(GONE)
        assert not _has_extension(session)
        assert "investigation_runs" in _tables(session)


def test_a_database_at_the_revision_that_was_main_s_head_for_a_day_is_migrated_too(
    migration_session_factory: sessionmaker[Session],
) -> None:
    """`m3n4o5p6q7r8` created `publication_vectors` and was the head of `main` before the
    removal. A developer's database, a copy, may be at it: the revision must still be
    found, and its table must go with the rest."""
    _needs_pgvector(migration_session_factory)
    url = _old_state(
        migration_session_factory,
        "m3n4o5p6q7r8",
        "CREATE TABLE publication_vectors (id integer PRIMARY KEY, embedding vector NOT NULL)",
        "INSERT INTO publication_vectors VALUES (1, '[1,2,3]')",
        "CREATE INDEX ON publication_vectors USING hnsw ((embedding::vector(3)) vector_cosine_ops)",
    )
    with migration_session_factory() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "m3n4o5p6q7r8"

    upgraded = _alembic(url, "upgrade", "head")

    assert upgraded.returncode == 0, upgraded.stderr
    with migration_session_factory() as session:
        assert not _tables(session) & set(GONE)
        assert not _has_extension(session)


@pytest.mark.parametrize(
    ("somebody_elses", "kept"),
    [
        # A column of the type, one of an array of it, one in another schema.
        (["CREATE TABLE theirs (embedding vector)"], "theirs"),
        (["CREATE TABLE theirs (embeddings vector[])"], "theirs"),
        (["CREATE SCHEMA other", "CREATE TABLE other.theirs (embedding vector)"], "theirs"),
        # A domain over the type, used by a column: the column's type is the domain's.
        (["CREATE DOMAIN embedding AS vector", "CREATE TABLE theirs (e embedding)"], "theirs"),
        # No column at all: a function that takes the type.
        (
            [
                (
                    "CREATE FUNCTION theirs(vector) RETURNS integer LANGUAGE sql "
                    "AS 'SELECT vector_dims($1)'"
                )
            ],
            None,
        ),
    ],
)
def test_whatever_else_depends_on_pgvector_keeps_it_and_the_migration_still_ends(
    migration_session_factory: sessionmaker[Session], somebody_elses: list[str], kept: str | None
) -> None:
    """The extension is dropped as the last thing of a removed subsystem, not as a rule.
    Anything else that depends on it — however indirectly — keeps it, loses nothing, and
    does not stop the migration: the semantic tables still go."""
    _needs_pgvector(migration_session_factory)
    url = _old_state(
        migration_session_factory,
        "l2m3n4o5p6q7",
        "CREATE TABLE semantic_vectors (embedding vector NOT NULL)",
        *somebody_elses,
    )

    upgraded = _alembic(url, "upgrade", "head")

    assert upgraded.returncode == 0, upgraded.stderr
    assert "The vector extension is kept: something outside" in upgraded.stderr
    with migration_session_factory() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "m4n5o6p7q8r9"
        assert _has_extension(session)
        assert not session.scalar(
            text("SELECT count(*) FROM pg_tables WHERE tablename = 'semantic_vectors'")
        )
        if kept is not None:
            assert session.scalar(
                text("SELECT count(*) FROM pg_tables WHERE tablename = :name"), {"name": kept}
            )
        else:
            assert session.scalar(text("SELECT count(*) FROM pg_proc WHERE proname = 'theirs'"))


def test_pgvector_another_role_installed_is_theirs_to_drop(
    migration_session_factory: sessionmaker[Session],
) -> None:
    url = _url(migration_session_factory)
    _needs_pgvector(migration_session_factory)
    assert _alembic(url, "downgrade", "l2m3n4o5p6q7").returncode == 0
    role = "administrator_of_the_test"
    with migration_session_factory.begin() as session:
        session.execute(text(f"DROP ROLE IF EXISTS {role}"))
        session.execute(text(f"CREATE ROLE {role} SUPERUSER"))
        session.execute(text(f"SET ROLE {role}"))
        session.execute(text("CREATE EXTENSION vector"))
        session.execute(text("RESET ROLE"))
        session.execute(text("CREATE TABLE semantic_vectors (embedding vector NOT NULL)"))
    try:
        upgraded = _alembic(url, "upgrade", "head")

        assert upgraded.returncode == 0, upgraded.stderr
        assert "The vector extension is kept: another role installed it" in upgraded.stderr
        with migration_session_factory() as session:
            assert _has_extension(session)
            assert not _tables(session) & set(GONE)
    finally:
        # The role is the server's, not the test database's: the extension it owns goes
        # with the database, the role must be dropped by hand.
        with migration_session_factory.begin() as session:
            session.execute(text("DROP EXTENSION IF EXISTS vector"))
            session.execute(text(f"DROP ROLE IF EXISTS {role}"))

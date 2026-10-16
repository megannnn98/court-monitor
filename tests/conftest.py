import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from db.database import create_database_engine, create_session_factory
from db.maintenance import truncate_disposable_tables

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_outside_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test asks a site: an investigation built from the environment gets no source
    outside the database. A test of a source search gives it a fake transport."""
    monkeypatch.setenv("INVESTIGATION_TELEGRAM_CHANNELS", "none")


def _truncate_test_tables(engine: Engine) -> None:
    truncate_disposable_tables(engine)


@pytest.fixture(scope="session")
def test_engine() -> Iterator[Engine]:
    database_url = os.environ.get("TEST_DATABASE_URL")

    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_database_engine(database_url)

    if engine.url.database != "court_monitor_test":
        raise RuntimeError("Integration tests may only use court_monitor_test")

    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(
    test_engine: Engine,
) -> Iterator[sessionmaker[Session]]:
    _truncate_test_tables(test_engine)

    yield create_session_factory(test_engine)

    _truncate_test_tables(test_engine)


def _run_outside_a_transaction(engine: Engine, statement: str) -> None:
    # CREATE and DROP DATABASE cannot run inside a transaction.
    with engine.connect() as connection:
        connection.execution_options(isolation_level="AUTOCOMMIT").execute(text(statement))


@pytest.fixture
def migration_session_factory(test_engine: Engine) -> Iterator[sessionmaker[Session]]:
    """A database of the test's own, at head, for a test that runs `alembic downgrade`.

    On the shared database every downgrade and upgrade dropped and re-added columns.
    PostgreSQL never frees the slot of a dropped column and a table holds at most 1600,
    so the shared database wore out. This one is made in the same server and dropped
    after the test, whatever the test did to it."""
    name = f"court_monitor_migration_{uuid.uuid4().hex[:12]}_test"
    database_url = test_engine.url.set(database=name).render_as_string(hide_password=False)
    _run_outside_a_transaction(test_engine, f'CREATE DATABASE "{name}"')
    engine = create_database_engine(database_url)
    try:
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            env={**os.environ, "DATABASE_URL": database_url},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert migrated.returncode == 0, migrated.stderr
        yield create_session_factory(engine)
    finally:
        engine.dispose()
        # FORCE: a test that failed may have left a connection open.
        _run_outside_a_transaction(test_engine, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(autouse=True)
def _no_operator_table_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """A check of the list must not read the operator's real table from a test: a link set
    in a developer's environment is a real one."""
    monkeypatch.delenv("RFM_TABLE_SHARE_URL", raising=False)


@pytest.fixture(autouse=True)
def _no_openrouter_balance_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page that shows the balance must not ask OpenRouter from a test: a key that a test
    sets is a fake one, and a developer's own key must not be spent on the network."""
    from web.ui import spend

    monkeypatch.setattr(spend, "_request", lambda _key: None)
    spend.reset_cache()


@pytest.fixture(autouse=True)
def _no_openrouter_catalog_or_stored_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """The model catalog is a fixed fake, never OpenRouter's; a page's warning reads no
    stored choice of a real database: a test that needs one sets `read_stored`."""
    from support.openrouter_catalog import CATALOG_PAYLOAD

    from llm import model_choice
    from web import entity_model

    fake = model_choice.CatalogCache(request=lambda: CATALOG_PAYLOAD)
    monkeypatch.setattr(model_choice, "CATALOG", fake)
    for module in ("web.ui.spend", "web.routers.entity_model", "processing.identity.model_probe"):
        monkeypatch.setattr(f"{module}.CATALOG", fake)
    monkeypatch.setattr(entity_model, "read_stored", lambda _kind="extraction": None)
    entity_model.forget()

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db.database import create_database_engine, create_session_factory
from db.maintenance import truncate_disposable_tables


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

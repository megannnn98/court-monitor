import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from database import create_database_engine, create_session_factory


def _truncate_test_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE TABLE
                    article_chunks,
                    parsed_articles,
                    source_documents,
                    sources
                RESTART IDENTITY CASCADE
                """
            )
        )


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

"""Engine settings: pre-ping, bounded pool and connect timeout, URL validation."""

from __future__ import annotations

import pytest
from sqlalchemy.pool import QueuePool

from db.database import (
    DatabaseConfigurationError,
    DatabasePoolSettings,
    create_database_engine,
    validate_database_url,
)

URL = "postgresql+psycopg://user:secret@localhost:5432/court_monitor"


def test_engine_pre_pings_and_bounds_the_pool() -> None:
    engine = create_database_engine(URL, DatabasePoolSettings(pool_size=3, max_overflow=2))

    pool = engine.pool
    assert isinstance(pool, QueuePool)
    assert pool._pre_ping is True
    assert (pool.size(), pool._max_overflow, pool._timeout) == (3, 2, 30)
    engine.dispose()


def test_pool_settings_from_env() -> None:
    settings = DatabasePoolSettings.from_env(
        {"DATABASE_POOL_SIZE": "8", "DATABASE_MAX_OVERFLOW": "0", "DATABASE_CONNECT_TIMEOUT": "3"}
    )
    assert settings == DatabasePoolSettings(
        pool_size=8, max_overflow=0, pool_timeout=30, connect_timeout=3
    )


@pytest.mark.parametrize(
    "env",
    [{"DATABASE_POOL_SIZE": "0"}, {"DATABASE_MAX_OVERFLOW": "-1"}, {"DATABASE_POOL_TIMEOUT": "x"}],
)
def test_invalid_pool_settings_are_rejected(env: dict[str, str]) -> None:
    with pytest.raises(DatabaseConfigurationError):
        DatabasePoolSettings.from_env(env)


@pytest.mark.parametrize(
    "url", ["not a url", "sqlite:///court.db", "postgresql+psycopg://user@localhost:5432/"]
)
def test_invalid_database_url_is_rejected(url: str) -> None:
    with pytest.raises(DatabaseConfigurationError):
        validate_database_url(url)


def test_valid_database_url_is_accepted() -> None:
    validate_database_url(URL)

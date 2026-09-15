from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker


class DatabaseConfigurationError(ValueError):
    pass


def _int_setting(env: Mapping[str, str], name: str, default: int, *, minimum: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise DatabaseConfigurationError(f"{name} must be an integer") from exc
    if value < minimum:
        raise DatabaseConfigurationError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True)
class DatabasePoolSettings:
    """Connection pool per process. Defaults suit one API worker or one Dagster run."""

    pool_size: int = 5
    max_overflow: int = 10
    # Seconds to wait for a free pooled connection before failing the request.
    pool_timeout: int = 30
    # Seconds to wait for PostgreSQL to accept a new connection (no infinite wait).
    connect_timeout: int = 10

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DatabasePoolSettings:
        env = os.environ if env is None else env
        return cls(
            pool_size=_int_setting(env, "DATABASE_POOL_SIZE", cls.pool_size, minimum=1),
            max_overflow=_int_setting(env, "DATABASE_MAX_OVERFLOW", cls.max_overflow, minimum=0),
            pool_timeout=_int_setting(env, "DATABASE_POOL_TIMEOUT", cls.pool_timeout, minimum=1),
            connect_timeout=_int_setting(
                env, "DATABASE_CONNECT_TIMEOUT", cls.connect_timeout, minimum=1
            ),
        )


def validate_database_url(database_url: str) -> None:
    """Fail at startup on a malformed or non-PostgreSQL URL, not on the first query."""
    try:
        url = make_url(database_url)
    except ArgumentError as exc:
        raise DatabaseConfigurationError("DATABASE_URL is not a valid SQLAlchemy URL") from exc
    if url.get_backend_name() != "postgresql":
        raise DatabaseConfigurationError("DATABASE_URL must be a PostgreSQL URL")
    if not url.database:
        raise DatabaseConfigurationError("DATABASE_URL must name a database")


def create_database_engine(database_url: str, pool: DatabasePoolSettings | None = None) -> Engine:
    pool = pool or DatabasePoolSettings()
    return create_engine(
        database_url,
        # A connection dropped by PostgreSQL restart or a proxy is replaced, not
        # handed to a request.
        pool_pre_ping=True,
        pool_size=pool.pool_size,
        max_overflow=pool.max_overflow,
        pool_timeout=pool.pool_timeout,
        connect_args={"connect_timeout": pool.connect_timeout},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

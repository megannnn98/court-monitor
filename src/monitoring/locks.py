"""Session-level PostgreSQL advisory locks for monitoring stages."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, func, select


@contextmanager
def advisory_lock(engine: Engine, key: str) -> Iterator[None]:
    """Hold `key` for the block on a dedicated connection; released on any exit.

    Blocking: a second holder waits instead of racing. The lock lives outside any
    transaction, so the stage inside keeps its own short unit-of-work transactions.
    """
    lock_id = func.hashtextextended(key, 0)
    with engine.connect() as connection:
        connection.execute(select(func.pg_advisory_lock(lock_id)))
        connection.commit()
        try:
            yield
        finally:
            connection.execute(select(func.pg_advisory_unlock(lock_id)))
            connection.commit()

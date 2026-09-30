"""Non-blocking PostgreSQL advisory locks for work that must happen once at a time.

`monitoring.locks.advisory_lock` waits for a held lock; this one never does, so the
caller can refuse instead of queueing. The lock is session-level and released on any
exit, including a crash of the process: nothing is left held by a dead worker.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, func, select


@contextmanager
def try_advisory_lock(engine: Engine, key: str) -> Iterator[bool]:
    """Hold `key` for the block on a dedicated connection.

    Yields True when the lock was taken and False when another session holds it. The
    lock lives outside any transaction, so the work inside keeps its own transactions.
    """
    lock_id = func.hashtextextended(key, 0)
    with engine.connect() as connection:
        acquired = bool(connection.execute(select(func.pg_try_advisory_lock(lock_id))).scalar())
        connection.commit()
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            connection.execute(select(func.pg_advisory_unlock(lock_id)))
            connection.commit()

"""Session-level PostgreSQL advisory locks for monitoring stages."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, func, select

DEFAULT_POLL_SECONDS = 1.0


@contextmanager
def advisory_lock(
    engine: Engine,
    key: str,
    *,
    while_waiting: Callable[[], None] | None = None,
    wait_callback_every_seconds: float = 30.0,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
) -> Iterator[None]:
    """Hold `key` for the block on a dedicated connection; released on any exit.

    Waits for a held lock by polling `pg_try_advisory_lock` instead of blocking, and
    calls `while_waiting` at most every `wait_callback_every_seconds` meanwhile — the
    monitoring run's heartbeat, which also raises if the run was aborted, so a live
    waiting run is neither declared stale nor left waiting after it was replaced.
    The lock lives outside any transaction, so the stage inside keeps its own short
    unit-of-work transactions.
    """
    lock_id = func.hashtextextended(key, 0)
    with engine.connect() as connection:
        last_callback = time.monotonic()
        while True:
            acquired = bool(connection.execute(select(func.pg_try_advisory_lock(lock_id))).scalar())
            connection.commit()
            if acquired:
                break
            now = time.monotonic()
            if while_waiting is not None and now - last_callback >= wait_callback_every_seconds:
                while_waiting()
                last_callback = now
            time.sleep(poll_seconds)
        try:
            yield
        finally:
            connection.execute(select(func.pg_advisory_unlock(lock_id)))
            connection.commit()

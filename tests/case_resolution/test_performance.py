"""Case resolution at a size: a batch costs a bounded number of queries, however many
events the database holds, and a second run reads and writes nothing of the first."""

from __future__ import annotations

import time

from case_resolution.bulk import seed_events
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from support.query_counter import count_queries

from cases.service import resolve


def test_a_batch_and_a_second_run_stay_cheap(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    events = seed_events(session_factory, people=300, per_person=5, prolific=200)

    started = time.monotonic()
    first = resolve(session_factory, batch_size=500)
    elapsed = time.monotonic() - started
    with count_queries(test_engine) as statements:
        second = resolve(session_factory, batch_size=500)

    assert first.events_seen == events
    assert first.opened + first.joined == events
    assert elapsed < 120
    # The second run reads the ids and which are linked: two queries a batch, plus the lock.
    assert (second.opened, second.joined, second.reattached) == (0, 0, 0)
    assert len(statements) <= 4 * second.batches + 4

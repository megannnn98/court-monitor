"""Resolve the person mentions of several articles in parallel worker processes.

Correctness rests on the identity-block locks `PersonResolutionService` already takes:
mentions whose names share a block are serialized across workers, so two workers never
create a duplicate person for the same name, and the second one sees the first one's
committed person. Locks are taken sorted inside one transaction, so workers holding
different blocks cannot deadlock.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

from db.database import DatabasePoolSettings, create_database_engine, create_session_factory
from extraction.resolution_service import ExtractionResolutionService, ResolutionStats
from persons.persistence import SqlAlchemyPersonPersistence

logger = logging.getLogger("person_resolution")

# Past this the database saturates: 600 articles took 36 s in one process, 16 s in four
# and 13 s in eight. Each worker also holds its own connection, and PostgreSQL here
# allows 100.
MAX_WORKERS = 8
# One connection per worker: a worker resolves its articles one after another.
_WORKER_POOL = DatabasePoolSettings(pool_size=1, max_overflow=0)


class ParallelResolutionError(RuntimeError):
    """A worker failed; the runs it did not finish are named so they can be re-run."""

    def __init__(self, failed_run_ids: list[int], cause: BaseException) -> None:
        super().__init__(
            f"Person resolution failed for runs {failed_run_ids}: {type(cause).__name__}"
        )
        self.failed_run_ids = failed_run_ids


def worker_count(workers: int, run_ids: Sequence[int]) -> int:
    """Never more workers than articles to resolve, and never more than the cap."""
    return max(1, min(workers, len(run_ids), MAX_WORKERS))


def resolve_runs(database_url: str, run_ids: Sequence[int], *, workers: int) -> ResolutionStats:
    """Resolve these extraction runs, in `workers` processes (1 = in this process)."""
    if workers < 1:
        raise ValueError("workers must be greater than zero")
    if not run_ids:
        return ResolutionStats()
    count = worker_count(workers, run_ids)
    if count == 1:
        return _resolve_chunk(database_url, list(run_ids))
    # Sorted, so the same request always splits the same way. This does not make the
    # result equal to a single process: the articles are processed in another order, and
    # which mention creates the person first decides how the later ones resolve. Measured
    # on a six-article fixture: 9 of 24 mentions map to another person than sequentially.
    chunks = [list(sorted(run_ids)[index::count]) for index in range(count)]
    logger.warning(
        "event=person_resolution_parallel workers=%d runs=%d "
        "note=decisions may differ from a single process; use for bulk rebuilds",
        count,
        len(run_ids),
    )
    total = ResolutionStats()
    with ProcessPoolExecutor(max_workers=count) as pool:
        running = {pool.submit(_resolve_chunk, database_url, chunk): chunk for chunk in chunks}
        failed: list[int] = []
        first_error: BaseException | None = None
        for future, chunk in running.items():
            try:
                total = _add(total, future.result())
            except Exception as exc:  # noqa: BLE001 - reported per chunk, re-raised below
                # Every other worker is still awaited, so the report names every run left
                # unresolved. An article is its own transaction: finished ones are
                # committed and a re-run skips them.
                logger.error(
                    "event=person_resolution_chunk_failed runs=%s error=%s",
                    chunk,
                    type(exc).__name__,
                )
                failed.extend(chunk)
                first_error = first_error or exc
    if first_error is not None:
        raise ParallelResolutionError(sorted(failed), first_error) from first_error
    return total


def _resolve_chunk(database_url: str, run_ids: list[int]) -> ResolutionStats:
    # The engine is created inside the worker: connections are never shared across
    # processes (the caller disposes its own engine before starting them).
    session_factory = create_session_factory(create_database_engine(database_url, _WORKER_POOL))
    service = ExtractionResolutionService(
        persistence=SqlAlchemyPersonPersistence(session_factory),
        session_factory=session_factory,
    )
    total = ResolutionStats()
    for run_id in run_ids:
        total = _add(total, service.resolve_extraction_run(run_id))
        logger.debug("event=person_resolution_run_completed run_id=%s", run_id)
    return total


def _add(left: ResolutionStats, right: ResolutionStats) -> ResolutionStats:
    return replace(
        left,
        mentions_processed=left.mentions_processed + right.mentions_processed,
        mentions_resolved=left.mentions_resolved + right.mentions_resolved,
        new_persons_created=left.new_persons_created + right.new_persons_created,
        events_linked=left.events_linked + right.events_linked,
        reviews_pending=left.reviews_pending + right.reviews_pending,
        mentions_reused=left.mentions_reused + right.mentions_reused,
    )

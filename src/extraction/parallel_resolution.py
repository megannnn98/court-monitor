"""Resolve the person mentions of several articles in parallel worker processes.

Correctness rests on the identity-block locks `PersonResolutionService` already takes:
mentions whose names share a block are serialized across workers, so two workers never
create a duplicate person for the same name, and the second one sees the first one's
committed person. Locks are taken sorted inside one transaction, so workers holding
different blocks cannot deadlock.
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

from db.database import create_database_engine, create_session_factory
from extraction.resolution_service import ExtractionResolutionService, ResolutionStats
from persons.persistence import SqlAlchemyPersonPersistence


def resolve_runs(database_url: str, run_ids: Sequence[int], *, workers: int) -> ResolutionStats:
    """Resolve these extraction runs, in `workers` processes (1 = in this process)."""
    if workers < 1:
        raise ValueError("workers must be greater than zero")
    if not run_ids:
        return ResolutionStats()
    if workers == 1:
        return _resolve_chunk(database_url, list(run_ids))
    chunks = [list(run_ids[index::workers]) for index in range(workers)]
    total = ResolutionStats()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for stats in pool.map(_resolve_chunk, [database_url] * len(chunks), chunks):
            total = _add(total, stats)
    return total


def _resolve_chunk(database_url: str, run_ids: list[int]) -> ResolutionStats:
    # The engine is created inside the worker: connections are never shared across processes.
    session_factory = create_session_factory(create_database_engine(database_url))
    service = ExtractionResolutionService(
        persistence=SqlAlchemyPersonPersistence(session_factory),
        session_factory=session_factory,
    )
    total = ResolutionStats()
    for run_id in run_ids:
        total = _add(total, service.resolve_extraction_run(run_id))
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

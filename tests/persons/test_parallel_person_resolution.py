"""Resolving articles in parallel worker processes (PostgreSQL)."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.person_resolution_fixtures import seed_mentions

from db.orm_models import EntityMentionRecord, PersonRecord
from extraction.parallel_resolution import (
    MAX_WORKERS,
    ParallelResolutionError,
    resolve_runs,
    worker_count,
)
from extraction.resolution_service import ResolutionStats

NAMES = [
    ("Иван Сергеевич Фролов", "Мария Петровна Белова"),
    ("Иван Сергеевич Фролов", "Олег Николаевич Тырышкин"),
    ("Мария Петровна Белова", "Олег Николаевич Тырышкин"),
    ("Азат Фанисович Мифтахов", "Иван Сергеевич Фролов"),
]


EXPECTED_PERSONS = [
    "Азат Фанисович Мифтахов",
    "Иван Сергеевич Фролов",
    "Мария Петровна Белова",
    "Олег Николаевич Тырышкин",
]


def _database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if url is None:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


def _seed(session_factory: sessionmaker[Session]) -> list[int]:
    return [seed_mentions(session_factory, *surfaces)[0] for surfaces in NAMES]


def _persons(session_factory: sessionmaker[Session]) -> list[str]:
    with session_factory() as session:
        return sorted(session.scalars(select(PersonRecord.canonical_name)).all())


def test_parallel_resolution_gives_the_same_persons_as_one_process(
    session_factory: sessionmaker[Session],
) -> None:
    database_url = _database_url()
    run_ids = _seed(session_factory)

    sequential = resolve_runs(database_url, run_ids, workers=1)

    assert sequential.mentions_processed == 8
    assert sequential.new_persons_created == 4
    assert _persons(session_factory) == EXPECTED_PERSONS


def test_parallel_workers_do_not_create_duplicate_persons(
    session_factory: sessionmaker[Session],
) -> None:
    """The same name in four articles resolved by four workers stays one person."""
    database_url = _database_url()
    run_ids = _seed(session_factory)

    stats = resolve_runs(database_url, run_ids, workers=4)

    assert stats.mentions_processed == 8
    assert stats.new_persons_created == 4
    assert _persons(session_factory) == EXPECTED_PERSONS


def test_workers_must_be_positive(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(ValueError, match="workers"):
        resolve_runs(_database_url(), [1], workers=0)


def test_nothing_to_resolve_is_not_an_error(session_factory: sessionmaker[Session]) -> None:
    assert resolve_runs(_database_url(), [], workers=4) == ResolutionStats()


def test_worker_count_never_exceeds_the_work_or_the_cap() -> None:
    """Review finding: --workers 1000 for one article started 1000 tasks, 999 of them empty."""
    assert worker_count(1000, run_ids=[1]) == 1
    assert worker_count(1000, run_ids=list(range(100))) == MAX_WORKERS
    assert worker_count(2, run_ids=list(range(100))) == 2


def test_a_failing_worker_names_the_runs_it_did_not_finish(
    session_factory: sessionmaker[Session],
) -> None:
    """Review finding: a worker error said nothing about which runs were left unresolved."""
    unreachable = (
        "postgresql+psycopg://court_monitor:court_monitor_dev@127.0.0.1:1/court_monitor_test"
    )

    with pytest.raises(ParallelResolutionError) as failure:
        resolve_runs(unreachable, [11, 22], workers=2)

    assert failure.value.failed_run_ids == [11, 22]
    assert "11" in str(failure.value) and "22" in str(failure.value)


def _digest(session_factory: sessionmaker[Session]) -> list[tuple[str, str | None]]:
    """Every person mention with the canonical name it was resolved to."""
    with session_factory() as session:
        names = {
            person.id: person.canonical_name for person in session.scalars(select(PersonRecord))
        }
        return [
            (
                mention.surface_text,
                names.get(mention.person_id) if mention.person_id is not None else None,
            )
            for mention in session.scalars(
                select(EntityMentionRecord).order_by(EntityMentionRecord.id)
            )
            if mention.entity_type == "person"
        ]


def test_one_process_resolves_the_same_way_every_time(
    session_factory: sessionmaker[Session],
) -> None:
    database_url = _database_url()

    resolve_runs(database_url, _seed(session_factory), workers=1)
    first = _digest(session_factory)

    assert first == _digest(session_factory)
    assert {name for _, name in first if name} == set(EXPECTED_PERSONS)


def test_parallel_resolution_keeps_the_persons_but_may_differ_in_single_decisions(
    session_factory: sessionmaker[Session],
) -> None:
    """What the parallel mode guarantees: the same people, not the same decision per mention.

    The articles are processed in another order, so which mention creates the person first
    changes; measured on a six-article fixture, 9 of 24 mentions mapped to another person.
    """
    database_url = _database_url()

    stats = resolve_runs(database_url, _seed(session_factory), workers=4)

    assert stats.new_persons_created == len(EXPECTED_PERSONS)
    assert {name for _, name in _digest(session_factory) if name} == set(EXPECTED_PERSONS)


def test_progress_is_reported_once_per_run_in_a_single_process(
    session_factory: sessionmaker[Session],
) -> None:
    run_ids = _seed(session_factory)
    steps: list[int] = []

    resolve_runs(_database_url(), run_ids, workers=1, on_progress=steps.append)

    assert steps == [1] * len(run_ids)


def test_progress_is_reported_per_chunk_across_worker_processes(
    session_factory: sessionmaker[Session],
) -> None:
    """A worker process cannot call back, so a chunk reports once it is finished."""
    run_ids = _seed(session_factory)
    steps: list[int] = []

    resolve_runs(_database_url(), run_ids, workers=2, on_progress=steps.append)

    # Two workers split four runs into two chunks, so progress arrives twice, by two.
    assert steps == [2, 2]


def test_resolution_runs_without_a_progress_callback(
    session_factory: sessionmaker[Session],
) -> None:
    run_ids = _seed(session_factory)

    assert resolve_runs(_database_url(), run_ids, workers=1).new_persons_created == 4

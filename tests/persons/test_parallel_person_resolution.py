"""Resolving articles in parallel worker processes (PostgreSQL)."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.person_resolution_fixtures import seed_mentions

from db.orm_models import PersonRecord
from extraction.parallel_resolution import resolve_runs
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

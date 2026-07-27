"""Unit tests: Rosfinmonitoring (fedsfm) adapter and import."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from court_monitor.services import import_rfm_records
from court_monitor.sources.fedsfm import PersonRow, load_fixture_rows, parse_rfm_csv
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import Base

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "rfm" / "persons.csv"


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_fixture_loads():
    rows = load_fixture_rows()
    assert len(rows) >= 1
    assert all(isinstance(r, PersonRow) for r in rows)


def test_parse_csv_fields():
    rows = parse_rfm_csv(FIXTURE_PATH)
    first = rows[0]
    assert first.raw_name
    assert first.normalized_name
    assert first.birth_date
    assert first.category


def test_name_normalization():
    rows = parse_rfm_csv(FIXTURE_PATH)
    ivanov = next(r for r in rows if "Иванов" in r.raw_name)
    assert ivanov.normalized_name
    assert ivanov.normalization_confidence >= 0.9


def test_date_normalization():
    rows = parse_rfm_csv(FIXTURE_PATH)
    for r in rows:
        if r.birth_date:
            # Should be ISO format YYYY-MM-DD
            assert len(r.birth_date) == 10
            assert r.birth_date[4] == "-"
            assert r.birth_date[7] == "-"


def test_import_creates_records(db_session):
    rows = load_fixture_rows()
    stats = import_rfm_records(db_session, rows)
    assert stats.total == len(rows)
    assert stats.imported == len(rows)
    assert stats.duplicates == 0


def test_import_deduplicates(db_session):
    rows = load_fixture_rows()
    stats1 = import_rfm_records(db_session, rows)
    stats2 = import_rfm_records(db_session, rows)
    assert stats1.imported == stats2.duplicates
    assert stats2.imported == 0
    assert repo.count_person_records(db_session) == stats1.total


def test_list_person_records(db_session):
    rows = load_fixture_rows()
    import_rfm_records(db_session, rows)
    records = repo.list_person_records(db_session)
    assert len(records) >= 1
    assert all(r.normalized_name for r in records)


def test_show_person_record(db_session):
    rows = load_fixture_rows()
    import_rfm_records(db_session, rows)
    records = repo.list_person_records(db_session)
    first = records[0]
    assert first.id
    assert first.raw_name
    assert first.normalized_name


def test_incomplete_row_still_imported(db_session):
    """A row with missing birth_place should still be imported."""
    rows = [
        PersonRow(
            raw_name="Тестов Тест Тестович",
            normalized_name="тестов тест тестович",
            normalization_confidence=0.95,
            birth_date="1990-01-01",
            birth_place=None,
            category="Тест",
            source_ref="999",
            added_date="2026-01-01",
            raw_line="test",
        )
    ]
    stats = import_rfm_records(db_session, rows)
    assert stats.imported == 1
    records = repo.list_person_records(db_session)
    assert records[0].birth_place is None


def test_birth_place_preserved(db_session):
    rows = load_fixture_rows()
    import_rfm_records(db_session, rows)
    records = repo.list_person_records(db_session)
    moscow = next((r for r in records if r.birth_place and "Москва" in r.birth_place), None)
    assert moscow is not None

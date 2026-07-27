"""Unit tests: Rosfinmonitoring (fedsfm) adapter and import."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from court_monitor.services import import_rfm_records
from court_monitor.sources.fedsfm import PersonRow, load_fixture_rows, parse_file, parse_rfm_csv
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import Base

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "rfm"
FIXTURE_CSV = FIXTURE_DIR / "persons.csv"
FIXTURE_XML = FIXTURE_DIR / "persons.xml"
FIXTURE_ZIP = FIXTURE_DIR / "persons.zip"


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


# --- CSV fixture tests ---


def test_fixture_loads():
    rows = load_fixture_rows()
    assert len(rows) >= 1
    assert all(isinstance(r, PersonRow) for r in rows)


def test_parse_csv_fields():
    rows = parse_rfm_csv(FIXTURE_CSV)
    first = rows[0]
    assert first.raw_name
    assert first.normalized_name
    assert first.search_name
    assert first.birth_date


def test_name_normalization():
    rows = parse_rfm_csv(FIXTURE_CSV)
    ivanov = next(r for r in rows if "Иванов" in r.raw_name)
    assert ivanov.normalized_name
    assert ivanov.normalization_confidence >= 0.9
    assert ivanov.search_name == ivanov.raw_name.lower()


def test_date_normalization():
    rows = parse_rfm_csv(FIXTURE_CSV)
    for r in rows:
        if r.birth_date:
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
    assert first.search_name
    assert first.normalization_method


def test_incomplete_row_still_imported(db_session):
    """A row with missing birth_place should still be imported."""
    rows = [
        PersonRow(
            raw_name="Тестов Тест Тестович",
            normalized_name="тестов тест тестович",
            search_name="тестов тест тестович",
            normalization_confidence=0.95,
            normalization_method="lowercase",
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


# --- XML fixture tests ---


def test_parse_xml():
    result = parse_file(FIXTURE_XML)
    assert result.format_detected == "xml"
    assert result.total_records == 3
    assert result.recognized == 3
    assert len(result.rows) == 3


def test_xml_fields():
    result = parse_file(FIXTURE_XML)
    first = result.rows[0]
    assert first.raw_name == "Иванов Иван Иванович"
    assert first.birth_date == "1980-01-01"
    assert first.birth_place == "г. Москва"


def test_xml_import(db_session):
    result = parse_file(FIXTURE_XML)
    stats = import_rfm_records(db_session, result.rows)
    assert stats.imported == 3
    assert stats.duplicates == 0


def test_xml_dedup(db_session):
    result = parse_file(FIXTURE_XML)
    import_rfm_records(db_session, result.rows)
    stats2 = import_rfm_records(db_session, result.rows)
    assert stats2.duplicates == 3


# --- ZIP fixture tests ---


def test_parse_zip():
    result = parse_file(FIXTURE_ZIP)
    assert "zip" in result.format_detected
    assert result.total_records == 3
    assert len(result.rows) == 3


def test_zip_import(db_session):
    result = parse_file(FIXTURE_ZIP)
    stats = import_rfm_records(db_session, result.rows)
    assert stats.imported == 3


# --- Error handling tests ---


def test_parse_unknown_format(tmp_path):
    bad = tmp_path / "test.xyz"
    bad.write_text("not a real file")
    result = parse_file(bad)
    assert result.format_detected == "unknown"
    assert len(result.errors) > 0


def test_parse_corrupt_xml(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text("<broken><not closed")
    result = parse_file(bad)
    assert len(result.errors) > 0


def test_parse_empty_csv(tmp_path):
    bad = tmp_path / "empty.csv"
    bad.write_text("ФИО,Дата рождения\n")
    result = parse_file(bad)
    assert result.rows == []


# --- Search name test ---


def test_search_name_is_lowercase():
    rows = parse_rfm_csv(FIXTURE_CSV)
    for row in rows:
        assert row.search_name == row.search_name.lower()
        assert row.normalization_method == "lowercase"
        # search_name should match normalized_name (both go through normalize_fio)
        assert row.search_name == row.normalized_name

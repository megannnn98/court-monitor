"""Unit tests: Rosfinmonitoring (fedsfm) adapter and import."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from court_monitor.services import import_rfm_records
from court_monitor.sources.fedsfm import (
    PersonRow,
    _normalize_date,
    _normalize_name,
    load_fixture_rows,
    parse_file,
    parse_rfm_csv,
)
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import Base

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "rfm"
FIXTURE_CSV = FIXTURE_DIR / "persons.csv"
FIXTURE_CSV_V2 = FIXTURE_DIR / "rosfinmonitoring-2.csv"
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


def test_import_refreshes_existing_record_fields(db_session):
    """A birth place corrected by a later export must update the record, not
    insert a second person — which is why find_person_record only keys on
    birth_place when there is no birth date to identify by."""
    first = PersonRow(
        raw_name="Иванов Иван Иванович",
        normalized_name="иванов иван иванович",
        search_name="иванов иван иванович",
        normalization_confidence=0.95,
        normalization_method="lowercase",
        birth_date="1980-01-01",
        birth_place="г. Москва",
        category="старое основание",
        source_ref="1",
        added_date="2026-01-01",
        raw_line="old",
        gender="м",
        country="Россия",
        region="Москва",
        extra_json='{"status": "old"}',
    )
    second = PersonRow(
        raw_name="Иванов Иван Иванович",
        normalized_name="иванов иван иванович",
        search_name="иванов иван иванович",
        normalization_confidence=0.95,
        normalization_method="lowercase",
        birth_date="1980-01-01",
        birth_place="г. Санкт-Петербург",
        category="новое основание",
        source_ref="2",
        added_date="2026-02-01",
        raw_line="new",
        gender="м",
        country="Россия",
        region="Санкт-Петербург",
        extra_json='{"status": "new"}',
    )

    stats1 = import_rfm_records(db_session, [first], source_url="file://old")
    stats2 = import_rfm_records(db_session, [second], source_url="file://new")

    assert stats1.imported == 1
    assert stats2.imported == 0
    assert stats2.updated == 1
    records = repo.list_person_records(db_session)
    assert len(records) == 1
    assert records[0].birth_place == "г. Санкт-Петербург"
    assert records[0].category == "новое основание"
    assert records[0].source_ref == "2"
    assert records[0].source_url == "file://new"
    assert records[0].raw_line == "new"
    assert records[0].extra_json == '{"status": "new"}'


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


# --- Date normalization edge cases ---


def test_normalize_date_iso_passthrough():
    assert _normalize_date("1983-06-15") == "1983-06-15"


def test_normalize_date_dd_mm_yyyy():
    assert _normalize_date("15.06.1983") == "1983-06-15"


def test_normalize_date_dd_mm_yyyy_single_digit_day_month():
    assert _normalize_date("5.6.1983") == "1983-06-05"


def test_normalize_date_dbf_yyyymmdd():
    """DBF stores dates as bare YYYYMMDD (no separators)."""
    assert _normalize_date("19830615") == "1983-06-15"


def test_normalize_date_malformed_passthrough_unchanged():
    """Unrecognized formats are returned unchanged, not dropped or guessed."""
    assert _normalize_date("дата неизвестна") == "дата неизвестна"


def test_normalize_date_empty_is_none():
    assert _normalize_date("") is None


# --- Name normalization confidence by token count ---


def test_normalize_name_confidence_three_tokens():
    norm, conf = _normalize_name("Иванов Иван Иванович")
    assert norm == "иванов иван иванович"
    assert conf == 0.95


def test_normalize_name_confidence_two_tokens():
    norm, conf = _normalize_name("Иванов Иван")
    assert conf == 0.70


def test_normalize_name_confidence_one_token():
    norm, conf = _normalize_name("Иванов")
    assert conf == 0.40


# --- dedup_key ---


def test_dedup_key_differs_by_birth_date():
    base = {
        "raw_name": "Иванов Иван Иванович",
        "normalized_name": "иванов иван иванович",
        "search_name": "иванов иван иванович",
        "normalization_confidence": 0.95,
        "normalization_method": "lowercase",
        "birth_place": None,
        "category": None,
        "source_ref": None,
        "added_date": None,
        "raw_line": "",
    }
    row_a = PersonRow(birth_date="1980-01-01", **base)
    row_b = PersonRow(birth_date="1983-01-01", **base)
    row_c = PersonRow(birth_date="1980-01-01", **base)
    assert row_a.dedup_key != row_b.dedup_key
    assert row_a.dedup_key == row_c.dedup_key


# --- CSV: rows without a name are skipped, not errored ---


def test_csv_row_without_fio_is_skipped(tmp_path):
    path = tmp_path / "partial.csv"
    path.write_text(
        "ФИО,Дата рождения\n"
        "Иванов Иван Иванович,01.01.1980\n"
        ",01.01.1990\n"  # no FIO — must be skipped
        "Петров Петр Петрович,02.02.1975\n",
        encoding="utf-8",
    )
    rows = parse_rfm_csv(path)
    assert len(rows) == 2
    assert all(r.raw_name for r in rows)


# --- ZIP edge cases ---


def test_zip_with_no_recognized_files(tmp_path):
    import zipfile  # noqa: PLC0415

    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.txt", "not a data file")

    result = parse_file(path)
    assert result.rows == []
    assert any("No XML or DBF" in e for e in result.errors)


def test_zip_that_is_actually_xml_falls_back(tmp_path):
    """Some published archives are XML content saved with a .zip extension."""
    path = tmp_path / "mislabeled.zip"
    path.write_text(FIXTURE_XML.read_text(encoding="utf-8"), encoding="utf-8")

    result = parse_file(path)
    assert "xml" in result.format_detected
    assert len(result.rows) == 3


# --- Unknown-extension format auto-detection ---


def test_unknown_extension_detects_xml_by_content(tmp_path):
    path = tmp_path / "data.dat"
    path.write_text(FIXTURE_XML.read_text(encoding="utf-8"), encoding="utf-8")

    result = parse_file(path)
    assert result.format_detected == "xml"
    assert len(result.rows) == 3


def test_unknown_extension_detects_dbf_by_magic_byte(tmp_path):
    """A DBF magic byte (0x03) with otherwise garbage content should be routed
    to the DBF parser (and fail there with a reported error), not silently
    treated as an unrecognized format."""
    path = tmp_path / "data.dat"
    path.write_bytes(bytes([0x03]) + b"not really a dbf file")

    result = parse_file(path)
    assert result.format_detected == "dbf"
    assert len(result.errors) > 0


def test_unknown_extension_no_match_reports_error(tmp_path):
    path = tmp_path / "data.dat"
    path.write_bytes(b"\xff\xfe\x00\x01random binary")

    result = parse_file(path)
    assert result.format_detected == "unknown"
    assert len(result.errors) > 0
    assert result.rows == []


# --- New CSV format (rosfinmonitoring-2.csv) tests ---
#
# FIXTURE_CSV_V2 has 22,250 rows — parsing it is not free. All tests in this
# section share one module-scoped parse instead of each re-parsing the whole
# file from scratch (the latter took the whole suite from ~2s to ~8s).


@pytest.fixture(scope="module")
def csv_v2_rows() -> list[PersonRow]:
    return parse_rfm_csv(FIXTURE_CSV_V2)


def test_parse_new_csv_format(csv_v2_rows):
    assert len(csv_v2_rows) > 20000
    assert all(r.raw_name for r in csv_v2_rows)
    assert all(r.normalized_name for r in csv_v2_rows)


def test_new_csv_fields(csv_v2_rows):
    first = csv_v2_rows[0]
    assert first.gender is not None
    assert first.country is not None
    assert first.region is not None
    assert first.category in ("терроризм", "экстремизм")
    assert first.added_date is not None
    assert first.birth_date is None  # new format has no birth date
    assert first.source_ref is None  # new format has no row number


def test_new_csv_birth_place_from_country_region(csv_v2_rows):
    with_region = [r for r in csv_v2_rows if r.region]
    assert len(with_region) > 1000
    for r in with_region[:10]:
        assert r.country in r.birth_place
        assert r.region in r.birth_place


def test_new_csv_extra_json(csv_v2_rows):
    minors = [r for r in csv_v2_rows if r.extra_json and "minor" in r.extra_json]
    assert len(minors) > 500
    parsed = json.loads(minors[0].extra_json)
    assert "age_at_inclusion" in parsed
    assert "status" in parsed


def test_new_csv_long_names(csv_v2_rows):
    """Central Asian 4+ token names parse correctly."""
    long_names = [r for r in csv_v2_rows if len(r.raw_name.split()) >= 4]
    assert len(long_names) > 400
    for r in long_names[:5]:
        assert len(r.normalized_name.split()) >= 4
        assert r.normalization_confidence == 0.95


def test_new_csv_import(db_session, csv_v2_rows):
    rows = csv_v2_rows[:10]  # import subset for speed
    stats = import_rfm_records(db_session, rows)
    assert stats.imported == 10
    records = repo.list_person_records(db_session)
    assert len(records) == 10
    assert records[0].gender is not None
    assert records[0].country is not None


def test_new_csv_dedup(db_session, csv_v2_rows):
    rows = csv_v2_rows[:5]
    stats1 = import_rfm_records(db_session, rows)
    stats2 = import_rfm_records(db_session, rows)
    assert stats1.imported == stats2.duplicates
    assert stats2.imported == 0


def test_new_csv_bom_handled(csv_v2_rows):
    """CSV with UTF-8 BOM (\\ufeff) parses correctly."""
    # BOM should not appear in any field values
    for r in csv_v2_rows[:100]:
        assert not r.raw_name.startswith("\ufeff")
        assert not r.normalized_name.startswith("\ufeff")


def test_new_csv_namesakes_from_different_regions_both_imported(db_session, csv_v2_rows):
    """Regression: the new format has no birth_date at all, so two different
    real people who happen to share a full name (common in a 22k-row Russian
    name list) must not collide during import just because both also lack a
    birth_date \u2014 verified against a real collision in this fixture
    ("\u042f\u043a\u043e\u0432\u043b\u0435\u0432 \u0410\u043b\u0435\u043a\u0441\u0430\u043d\u0434\u0440 \u041d\u0438\u043a\u043e\u043b\u0430\u0435\u0432\u0438\u0447" from two different regions)."""
    namesakes = [
        r
        for r in csv_v2_rows
        if r.normalized_name
        == "\u044f\u043a\u043e\u0432\u043b\u0435\u0432 \u0430\u043b\u0435\u043a\u0441\u0430\u043d\u0434\u0440 \u043d\u0438\u043a\u043e\u043b\u0430\u0435\u0432\u0438\u0447"
    ]
    assert len(namesakes) == 2
    assert namesakes[0].region != namesakes[1].region

    stats = import_rfm_records(db_session, namesakes)
    assert stats.imported == 2
    assert stats.duplicates == 0

    records = [
        r
        for r in repo.list_person_records(db_session)
        if r.normalized_name
        == "\u044f\u043a\u043e\u0432\u043b\u0435\u0432 \u0430\u043b\u0435\u043a\u0441\u0430\u043d\u0434\u0440 \u043d\u0438\u043a\u043e\u043b\u0430\u0435\u0432\u0438\u0447"
    ]
    assert len(records) == 2
    assert {r.region for r in records} == {namesakes[0].region, namesakes[1].region}

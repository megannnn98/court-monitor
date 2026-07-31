"""Importing the registry against a preloaded index rather than row by row.

The list runs to ~22k entries. Looking each one up separately meant 22k round
trips — and after a ``--replace`` every one of them was guaranteed to miss.
These tests pin the behaviour that had to survive the change: the dedup key,
in-import collapsing, and refreshing an existing record in place.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from court_monitor.services import import_rfm_records
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import PersonRecord


@dataclass
class _Row:
    """The shape import_rfm_records reads off a parsed registry row."""

    raw_name: str
    normalized_name: str
    search_name: str
    normalization_confidence: float = 1.0
    normalization_method: str = "test"
    birth_date: str | None = None
    birth_place: str | None = None
    category: str | None = None
    source_ref: str | None = None
    added_date: str | None = None
    raw_line: str = ""
    gender: str | None = None
    country: str | None = None
    region: str | None = None
    extra_json: str | None = None


def _row(name: str, **kwargs) -> _Row:
    return _Row(raw_name=name, normalized_name=name.lower(), search_name=name.lower(), **kwargs)


def _count(session) -> int:
    return int(session.execute(select(func.count(PersonRecord.id))).scalar_one())


def test_a_fresh_import_inserts_everything(db_session):
    stats = import_rfm_records(
        db_session,
        [
            _row("Иванов Иван", birth_date="1983-01-01"),
            _row("Петров Пётр", birth_date="1990-02-02"),
        ],
    )

    assert (stats.imported, stats.updated, stats.duplicates) == (2, 0, 0)
    assert _count(db_session) == 2


def test_re_importing_the_same_list_changes_nothing(db_session):
    rows = [_row("Иванов Иван", birth_date="1983-01-01")]
    import_rfm_records(db_session, rows)

    stats = import_rfm_records(db_session, rows)

    assert (stats.imported, stats.updated, stats.duplicates) == (0, 0, 1)
    assert _count(db_session) == 1


def test_two_identical_rows_inside_one_import_collapse(db_session):
    """The index has to see a row added moments earlier, or the unique
    constraint is hit on flush instead of the duplicate being counted."""
    stats = import_rfm_records(
        db_session,
        [
            _row("Иванов Иван", birth_date="1983-01-01"),
            _row("Иванов Иван", birth_date="1983-01-01"),
        ],
    )

    assert (stats.imported, stats.duplicates) == (1, 1)
    assert _count(db_session) == 1


def test_a_changed_field_refreshes_the_stored_record(db_session):
    import_rfm_records(db_session, [_row("Иванов Иван", birth_date="1983-01-01")])

    stats = import_rfm_records(
        db_session, [_row("Иванов Иван", birth_date="1983-01-01", category="Перечень 2")]
    )

    assert (stats.imported, stats.updated, stats.duplicates) == (0, 1, 0)
    stored = db_session.execute(select(PersonRecord)).scalar_one()
    assert stored.category == "Перечень 2"


def test_a_birth_date_pins_identity_so_a_corrected_place_updates(db_session):
    """With a date present the place is a mutable detail — keying on it would
    turn every correction into a second person."""
    import_rfm_records(
        db_session, [_row("Иванов Иван", birth_date="1983-01-01", birth_place="Г. МОСКВА")]
    )

    stats = import_rfm_records(
        db_session, [_row("Иванов Иван", birth_date="1983-01-01", birth_place="Г. МОСКВА, РОССИЯ")]
    )

    assert stats.updated == 1
    assert _count(db_session) == 1


def test_without_a_date_the_place_separates_two_namesakes(db_session):
    """The CSV export carries no birth dates, and name alone collapsed people
    who merely share a common Russian full name."""
    stats = import_rfm_records(
        db_session,
        [
            _row("Яковлев Александр Николаевич", birth_place="ЗАБАЙКАЛЬСКИЙ КРАЙ"),
            _row("Яковлев Александр Николаевич", birth_place="КРАСНОДАРСКИЙ КРАЙ"),
        ],
    )

    assert stats.imported == 2
    assert _count(db_session) == 2


def test_records_of_another_source_are_not_touched(db_session):
    import_rfm_records(db_session, [_row("Иванов Иван", birth_date="1983-01-01")], source="other")

    stats = import_rfm_records(db_session, [_row("Иванов Иван", birth_date="1983-01-01")])

    assert stats.imported == 1
    assert _count(db_session) == 2


def test_the_index_matches_the_sql_lookup(db_session):
    """Two implementations of one dedup rule: they must agree, or an import
    inserts a row the constraint then rejects."""
    import_rfm_records(
        db_session,
        [
            _row("Иванов Иван", birth_date="1983-01-01", birth_place="Г. МОСКВА"),
            _row("Сидоров Пётр", birth_place="Г. УФА"),
        ],
    )

    index = repo.load_person_record_index(db_session, source="rfm")
    for stored in db_session.execute(select(PersonRecord)).scalars():
        via_sql = repo.find_person_record(
            db_session,
            source=stored.source,
            normalized_name=stored.normalized_name,
            birth_date=stored.birth_date,
            birth_place=stored.birth_place,
        )
        assert index[repo.person_record_key(stored)] is via_sql

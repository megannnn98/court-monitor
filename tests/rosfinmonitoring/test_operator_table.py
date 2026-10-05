"""The operator's own table of the list: what is read from it, and what it may change.

It fills the days the ОВД-Инфо copy leaves empty and never moves one that copy gave; and
it is kept as a copy that a short read cannot empty.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from itertools import count

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    RfmOperatorEntryRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from rosfinmonitoring.inclusion_dates import InclusionDates, normalize_name
from rosfinmonitoring.inclusion_store import (
    carry_dates_from_previous_snapshot,
    write_inclusion_dates,
)
from rosfinmonitoring.operator_table import (
    SOURCE,
    OperatorRow,
    OperatorTableUnavailable,
    inclusion_dates,
    parse_rows,
    share_url,
    store,
)

IVANOV = "ИВАНОВ ИВАН ИВАНОВИЧ"
BORN = date(1980, 5, 1)


_hashes = count()


def _snapshot(session: Session) -> int:
    moment = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
    snapshot = RosfinmonitoringSnapshotRecord(
        snapshot_date=moment,
        source_url="https://www.fedsfm.ru/documents/terrorists-catalog-portal-act",
        content_hash=f"operator-table-{next(_hashes)}",
        entry_count=1,
        fetched_at=moment,
    )
    session.add(snapshot)
    session.commit()
    return snapshot.id


def _entry(session: Session, snapshot_id: int, full_name: str, born: date) -> int:
    entry = RosfinmonitoringEntryRecord(
        snapshot_id=snapshot_id,
        full_name=full_name,
        normalized_name=full_name.lower(),
        matching_key=full_name.lower().replace(" ", ""),
        birth_date=datetime(born.year, born.month, born.day, tzinfo=UTC),
        status="active",
        raw_data={},
    )
    session.add(entry)
    session.commit()
    return entry.id


def _dates(*rows: tuple[str, date, date]) -> InclusionDates:
    """What the ОВД-Инфо copy says: a name, a birth date, the day of inclusion."""
    return InclusionDates(
        by_name_and_birth={(normalize_name(name), born): added for name, born, added in rows},
        total_rows=len(rows),
        rows_without_added_date=0,
        names_in_source=frozenset(normalize_name(name) for name, _, _ in rows),
    )


def _row(
    name: str = IVANOV,
    born: date | None = BORN,
    added: date | None = date(2024, 3, 14),
    *,
    removed: bool = False,
) -> OperatorRow:
    return OperatorRow(name, born, "Г. САРАТОВ", added, removed, "терроризм", "")


def _source(session: Session, entry_id: int) -> tuple[datetime | None, str | None]:
    entry = session.get(RosfinmonitoringEntryRecord, entry_id)
    return entry.inclusion_date, entry.inclusion_source


def test_a_row_of_the_export_is_read_the_way_the_table_writes_it() -> None:
    rows = parse_rows(
        [
            {
                "ФИО": "ИВАНОВ ИВАН ИВАНОВИЧ*",
                # The export writes the month first: the sixth of July, not the seventh of June.
                "Дата добавления": "7/6/2011",
                "Дата рождения": "2/14/1986",
                "Место рождения": "Г. САРАТОВ;",
                "Удаление": "ИВАНОВ ИВАН ИВАНОВИЧ*",
                "Calculation": "терроризм",
                "категория": "война,украинцы",
            },
            {"ФИО": "ПЕТРОВ ПЁТР", "Дата добавления": "вчера", "Дата рождения": "13/40/1990"},
            {"ФИО": "  ", "Дата добавления": "1/1/2020"},
        ]
    )

    assert rows == [
        OperatorRow(
            "ИВАНОВ ИВАН ИВАНОВИЧ",
            date(1986, 2, 14),
            "Г. САРАТОВ",
            date(2011, 7, 6),
            True,
            "терроризм",
            "война,украинцы",
        ),
        # A day that is not a day is left empty, not guessed; the row is still a row.
        OperatorRow("ПЕТРОВ ПЁТР", None, "", None, False, "", ""),
    ]
    assert rows[1].normalized_name == "петров петр"


def test_the_link_is_a_setting_and_nothing_is_read_without_it() -> None:
    assert share_url({}) == ""
    assert share_url({"RFM_TABLE_SHARE_URL": " https://airtable.com/appX/shrY "}) == (
        "https://airtable.com/appX/shrY"
    )


def test_the_copy_is_replaced_whole_and_a_short_read_leaves_it(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        store(session, [_row(f"ИВАНОВ ИВАН {n}") for n in "АБВГ"])
        store(session, [_row("ПЕТРОВ ПЕТР ПЕТРОВИЧ"), _row("СИДОРОВ ИВАН"), _row("ИВАНОВ А")])
        names = session.scalars(select(RfmOperatorEntryRecord.full_name)).all()
        assert sorted(names) == ["ИВАНОВ А", "ПЕТРОВ ПЕТР ПЕТРОВИЧ", "СИДОРОВ ИВАН"]

        # A view filtered by mistake, or a download cut short: one row of three.
        with pytest.raises(OperatorTableUnavailable):
            store(session, [_row("ПЕТРОВ ПЕТР ПЕТРОВИЧ")])
        with pytest.raises(OperatorTableUnavailable):
            store(session, [])
        assert session.scalar(select(func.count()).select_from(RfmOperatorEntryRecord)) == 3


def test_a_removed_row_dates_nobody_and_of_two_listings_the_later_stands(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        store(
            session,
            [
                _row(added=date(2019, 1, 10), removed=True),
                _row(added=date(2023, 5, 2)),
                _row(added=date(2021, 5, 2)),
                _row("ПЕТРОВ ПЕТР ПЕТРОВИЧ", added=date(2020, 2, 2), removed=True),
                _row("СИДОРОВ ИВАН", born=None),
            ],
        )
        dates = inclusion_dates(session)

    assert dates.by_name_and_birth == {("иванов иван иванович", BORN): date(2023, 5, 2)}
    assert dates.total_rows == 5


def test_the_table_fills_an_empty_day_and_says_whose_day_it_is(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        snapshot_id = _snapshot(session)
        entry_id = _entry(session, snapshot_id, IVANOV, BORN)
        store(session, [_row()])
        result = write_inclusion_dates(
            session, snapshot_id, inclusion_dates(session), source=SOURCE
        )

        assert result.dated == 1
        assert _source(session, entry_id) == (datetime(2024, 3, 14, tzinfo=UTC), "operator")


def test_a_day_of_the_ovd_info_copy_is_not_moved_by_the_table(
    session_factory: sessionmaker[Session],
) -> None:
    """The two disagree on some 550 entries. The copy that records the list day by day
    stays; the table typed by hand only fills."""
    with session_factory() as session:
        snapshot_id = _snapshot(session)
        entry_id = _entry(session, snapshot_id, IVANOV, BORN)
        write_inclusion_dates(session, snapshot_id, _dates((IVANOV, BORN, date(2024, 3, 1))))
        store(session, [_row(added=date(2024, 3, 14))])
        result = write_inclusion_dates(
            session, snapshot_id, inclusion_dates(session), source=SOURCE
        )

        assert result.dated == 0
        assert _source(session, entry_id) == (datetime(2024, 3, 1, tzinfo=UTC), None)


def test_the_ovd_info_copy_replaces_a_day_the_table_stood_in_for(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        snapshot_id = _snapshot(session)
        entry_id = _entry(session, snapshot_id, IVANOV, BORN)
        other_id = _entry(session, snapshot_id, "ПЕТРОВ ПЕТР ПЕТРОВИЧ", BORN)
        store(session, [_row(), _row("ПЕТРОВ ПЕТР ПЕТРОВИЧ")])
        write_inclusion_dates(session, snapshot_id, inclusion_dates(session), source=SOURCE)

        # The copy now publishes one of the two.
        result = write_inclusion_dates(
            session, snapshot_id, _dates((IVANOV, BORN, date(2024, 3, 1)))
        )

        assert _source(session, entry_id) == (datetime(2024, 3, 1, tzinfo=UTC), None)
        # The other keeps the table's day, and is not counted as an entry without one.
        assert _source(session, other_id) == (datetime(2024, 3, 14, tzinfo=UTC), "operator")
        assert (result.dated, result.already_dated, result.unmatched) == (1, 1, {})


def test_a_carried_day_keeps_whose_day_it_is(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        first = _snapshot(session)
        _entry(session, first, IVANOV, BORN)
        store(session, [_row()])
        write_inclusion_dates(session, first, inclusion_dates(session), source=SOURCE)
        session.commit()
        second = _snapshot(session)
        entry_id = _entry(session, second, IVANOV, BORN)

    assert carry_dates_from_previous_snapshot(session_factory, second) == 1
    with session_factory() as session:
        assert _source(session, entry_id) == (datetime(2024, 3, 14, tzinfo=UTC), "operator")

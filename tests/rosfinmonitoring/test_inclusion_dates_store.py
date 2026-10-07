"""Writing the day of inclusion onto our entries.

What is written is the day an **entry** of the перечень appeared. It is not a fact about
a person, and the tests here are as much about what must not be written as about what
must: no date on a namesake, and no date lost when the list is downloaded again.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from itertools import count

import pytest
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import RosfinmonitoringEntryRecord, RosfinmonitoringSnapshotRecord
from rosfinmonitoring.inclusion_dates import InclusionDates, read_inclusion_dates
from rosfinmonitoring.inclusion_store import write_inclusion_dates

SNAPSHOT_DATE = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)


def _dates(*rows: tuple[str, date | None, date | None]) -> InclusionDates:
    """The ОВД-Инфо file's three columns, as the reader returns it."""
    import pyarrow
    from pyarrow import parquet

    payload = [
        {
            "name": name,
            "birth_date": datetime(birth.year, birth.month, birth.day, tzinfo=UTC)
            if birth
            else None,
            "added_date": datetime(added.year, added.month, added.day, tzinfo=UTC)
            if added
            else None,
        }
        for name, birth, added in rows
    ]
    names = ["name", "birth_date", "added_date"]
    table = pyarrow.table({name: [row[name] for row in payload] for name in names})
    sink = io.BytesIO()
    parquet.write_table(table, sink)
    return read_inclusion_dates(sink.getvalue())


_snapshot_counter = count()


def _snapshot(session: Session) -> int:
    snapshot = RosfinmonitoringSnapshotRecord(
        snapshot_date=SNAPSHOT_DATE,
        source_url="https://www.fedsfm.ru/documents/terrorists-catalog-portal-act",
        # Unique per snapshot, as the column is: two downloads of an unchanged page are
        # not stored twice, so a test that wants two snapshots must say they differ.
        content_hash=f"page-hash-{next(_snapshot_counter)}",
        entry_count=1,
        fetched_at=SNAPSHOT_DATE,
    )
    session.add(snapshot)
    session.commit()
    return snapshot.id


def _entry(
    session: Session,
    snapshot_id: int,
    full_name: str,
    birth_date: date | None = None,
    inclusion_date: datetime | None = None,
) -> int:
    entry = RosfinmonitoringEntryRecord(
        snapshot_id=snapshot_id,
        full_name=full_name,
        normalized_name=full_name.lower(),
        matching_key=full_name.lower().replace(" ", ""),
        birth_date=datetime(birth_date.year, birth_date.month, birth_date.day, tzinfo=UTC)
        if birth_date
        else None,
        inclusion_date=inclusion_date,
        status="active",
        raw_data={},
    )
    session.add(entry)
    session.commit()
    return entry.id


def _inclusion_date(session: Session, entry_id: int) -> datetime | None:
    entry = session.get(RosfinmonitoringEntryRecord, entry_id)
    assert entry is not None
    return entry.inclusion_date


def test_a_day_is_written_where_the_name_and_the_birth_date_both_agree(
    session_factory: sessionmaker[Session],
) -> None:
    dates = _dates(("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), date(2024, 3, 14)))

    with session_factory() as session:
        snapshot_id = _snapshot(session)
        entry_id = _entry(session, snapshot_id, "ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1))
        result = write_inclusion_dates(session, snapshot_id, dates)

        assert result.dated == 1
        assert _inclusion_date(session, entry_id) == datetime(2024, 3, 14, tzinfo=UTC)


def test_a_namesake_gets_no_date(
    session_factory: sessionmaker[Session],
) -> None:
    """Same name, another day of birth. The day in the file belongs to the person it
    describes, and writing it on this entry would date the wrong person without anything
    on the page saying so."""
    dates = _dates(("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), date(2024, 3, 14)))

    with session_factory() as session:
        snapshot_id = _snapshot(session)
        entry_id = _entry(session, snapshot_id, "ИВАНОВ ИВАН ИВАНОВИЧ", date(1975, 9, 9))
        result = write_inclusion_dates(session, snapshot_id, dates)

        assert result.dated == 0
        assert _inclusion_date(session, entry_id) is None
        assert result.unmatched["birth_dates_differ"] == 1


def test_a_day_and_a_month_their_way_round_gets_no_date_and_is_counted_separately(
    session_factory: sessionmaker[Session],
) -> None:
    """126 entries read 06.11 where we hold 11.06. It looks like one person written
    twice; it might be two people. Nothing in either list decides it, so no date is
    written — and the case is counted under its own name rather than lost in a total,
    because a number that moves is a number somebody will ask about."""
    dates = _dates(("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 6, 11), date(2024, 3, 14)))

    with session_factory() as session:
        snapshot_id = _snapshot(session)
        entry_id = _entry(session, snapshot_id, "ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 11, 6))
        result = write_inclusion_dates(session, snapshot_id, dates)

        assert result.dated == 0
        assert _inclusion_date(session, entry_id) is None
        assert result.unmatched["day_month_swapped"] == 1


def test_an_entry_without_a_birth_date_gets_none_even_with_a_unique_name(
    session_factory: sessionmaker[Session],
) -> None:
    """The name is unique in the file, so a name-only match would land on exactly one
    row. That is still a name-only match, and it is the case the whole rule exists for."""
    dates = _dates(("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), date(2024, 3, 14)))

    with session_factory() as session:
        snapshot_id = _snapshot(session)
        entry_id = _entry(session, snapshot_id, "ИВАНОВ ИВАН ИВАНОВИЧ")
        result = write_inclusion_dates(session, snapshot_id, dates)

        assert result.dated == 0
        assert _inclusion_date(session, entry_id) is None
        assert result.unmatched["no_birth_date"] == 1


def test_a_day_established_earlier_survives_a_new_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    """The state publishes a snapshot with no dates in it, and the file is read again for
    each one. A day already written must not go back to being unknown because the list
    was downloaded afresh — and a download that failed entirely changes nothing at all."""
    dates = _dates(("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), date(2024, 3, 14)))
    earlier = datetime(2024, 3, 14, tzinfo=UTC)

    with session_factory() as session:
        first = _snapshot(session)
        entry_id = _entry(session, first, "ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), earlier)
        write_inclusion_dates(session, first, dates)

        second = _snapshot(session)
        _entry(session, second, "ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1))
        # The new file covers nobody: an empty read, as a failed download would leave it.
        result = write_inclusion_dates(session, second, read_inclusion_dates(_empty_file()))

        assert result.dated == 0
        assert _inclusion_date(session, entry_id) == earlier, (
            "a day already known must not be un-established by a snapshot without dates"
        )


def test_a_day_already_written_is_not_moved_by_a_later_read(
    session_factory: sessionmaker[Session],
) -> None:
    """The file is somebody else's build and can be rebuilt differently. A day we have
    already established is not re-decided on every run: an entry written on 14 March stays
    on 14 March even if a later read of the file offers another day for the same person,
    because a date that moves under the operator is a date nobody can rely on."""
    earlier = datetime(2024, 3, 14, tzinfo=UTC)
    later = _dates(("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), date(2025, 1, 9)))

    with session_factory() as session:
        snapshot_id = _snapshot(session)
        entry_id = _entry(session, snapshot_id, "ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), earlier)
        result = write_inclusion_dates(session, snapshot_id, later)

        assert result.dated == 0
        assert result.already_dated == 1
        assert _inclusion_date(session, entry_id) == earlier


def test_the_write_covers_only_the_snapshot_it_was_asked_about(
    session_factory: sessionmaker[Session],
) -> None:
    dates = _dates(("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), date(2024, 3, 14)))

    with session_factory() as session:
        wanted = _snapshot(session)
        other = _snapshot(session)
        wanted_entry = _entry(session, wanted, "ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1))
        other_entry = _entry(session, other, "ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1))
        result = write_inclusion_dates(session, wanted, dates)

        assert result.entries == 1
        assert _inclusion_date(session, wanted_entry) is not None
        assert _inclusion_date(session, other_entry) is None, (
            "an older snapshot is a record of what the list was then; writing today's "
            "dates into it would be a claim about the past"
        )


def test_the_counts_add_up_to_the_entries(
    session_factory: sessionmaker[Session],
) -> None:
    """The operator is shown the numbers behind the dates, so they have to account for
    every entry: a total that does not add up hides entries nobody counted."""
    dates = _dates(
        ("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), date(2024, 3, 14)),
        ("ПЕТРОВ ПЕТР ПЕТРОВИЧ", date(1970, 1, 2), None),
    )

    with session_factory() as session:
        snapshot_id = _snapshot(session)
        _entry(session, snapshot_id, "ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1))
        _entry(session, snapshot_id, "ПЕТРОВ ПЕТР ПЕТРОВИЧ", date(1970, 1, 2))
        _entry(session, snapshot_id, "СИДОРОВ СИДОР СИДОРОВИЧ", date(1990, 1, 1))
        result = write_inclusion_dates(session, snapshot_id, dates)

        assert result.entries == 3
        assert result.dated + sum(result.unmatched.values()) == result.entries
        assert result.unmatched["source_without_birth_date"] == 1
        assert result.unmatched["name_absent_from_source"] == 1


def _empty_file() -> bytes:
    import pyarrow
    from pyarrow import parquet

    table = pyarrow.table(
        {
            "name": pyarrow.array([], type=pyarrow.string()),
            "birth_date": pyarrow.array([], type="timestamp[us]"),
            "added_date": pyarrow.array([], type="timestamp[us]"),
        }
    )
    sink = io.BytesIO()
    parquet.write_table(table, sink)
    return sink.getvalue()


def test_the_summary_reads_as_a_sentence_an_operator_can_check(
    session_factory: sessionmaker[Session],
) -> None:
    dates = _dates(("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), date(2024, 3, 14)))

    with session_factory() as session:
        snapshot_id = _snapshot(session)
        _entry(session, snapshot_id, "ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1))
        _entry(session, snapshot_id, "СИДОРОВ СИДОР СИДОРОВИЧ", date(1990, 1, 1))
        summary = write_inclusion_dates(session, snapshot_id, dates).summary()

    assert "записей=2" in summary
    assert "с датой=1" in summary
    assert "не сопоставлено=1" in summary


@pytest.mark.parametrize("reason", ["day_month_swapped", "no_birth_date"])
def test_every_refusal_is_one_of_the_named_reasons(
    session_factory: sessionmaker[Session], reason: str
) -> None:
    from rosfinmonitoring.inclusion_store import UNMATCHED_REASONS

    assert reason in UNMATCHED_REASONS

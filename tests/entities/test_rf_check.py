"""The person entities checked against a fresh Rosfinmonitoring list, on PostgreSQL."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    EntityPairDecisionRecord,
    RfmOperatorEntryRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from entities.rf_check import EntityRfCheck, match_level
from rosfinmonitoring.inclusion_dates import (
    InclusionDates,
    InclusionDatesUnavailable,
    normalize_name,
)
from rosfinmonitoring.operator_table import OperatorRow, OperatorTableUnavailable
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup

PERSONS = """
    <li>1. АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ*, 08.06.1996 г.р. , П. МАМЕДКАЛА;</li>
    <li>2. ИВАНОВ ИВАН ИВАНОВИЧ*, , ;</li>
"""


def _page(persons: str = PERSONS) -> bytes:
    return f"""
<div class="panel-group">
  <div class="panel-heading"><h4>Национальная часть</h4></div>
  <div class="panel-heading"><h4>Физические лица</h4></div>
  <div class="panel-body"><ol>{persons}</ol></div>
</div>
""".encode()


def _entities(session_factory: sessionmaker[Session], *names: str) -> None:
    with session_factory.begin() as session:
        for name in names:
            session.add(
                EntityGroupRecord(
                    key=name.lower(),
                    name=name,
                    variants=[[name, 1]],
                    mention_count=1,
                    article_count=1,
                    event_types={},
                )
            )


def _levels(session_factory: sessionmaker[Session]) -> dict[str, list[str]]:
    with session_factory() as session:
        rows = session.execute(
            select(EntityGroupRecord.name, EntityGroupRfMatchRecord.level)
            .join(EntityGroupRecord, EntityGroupRecord.id == EntityGroupRfMatchRecord.group_id)
            .order_by(EntityGroupRecord.name, EntityGroupRfMatchRecord.level)
        ).all()
    levels: dict[str, list[str]] = {}
    for name, level in rows:
        levels.setdefault(name, []).append(level)
    return levels


def _snapshots(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count(RosfinmonitoringSnapshotRecord.id))) or 0


def _inclusion_dates(
    *rows: tuple[str, str, str],
    unreadable: bool = False,
) -> Callable[[httpx.Client], InclusionDates]:
    """The ОВД-Инфо file as the check reads it, or a stand-in that always refuses.

    Injected the way the download is: the check takes an `httpx.Client`, so a test gives
    it a transport rather than a socket. The default answers with a file covering the
    published people, which is what the real one does.
    """
    if unreadable:

        def refuses(client: httpx.Client) -> InclusionDates:
            raise InclusionDatesUnavailable("файл не читается")

        return refuses

    def answered(client: httpx.Client) -> InclusionDates:
        by_name: dict[tuple[str, date], date] = {}
        for name, birth, added in rows:
            by_name[(normalize_name(name), date.fromisoformat(birth))] = date.fromisoformat(added)
        return InclusionDates(
            by_name_and_birth=by_name,
            total_rows=len(rows),
            rows_without_added_date=0,
            names_in_source=frozenset(name for name, _, _ in rows),
        )

    return answered


PUBLISHED_INCLUSION = (
    ("АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ", "1996-06-08", "2024-03-14"),
    ("ИВАНОВ ИВАН ИВАНОВИЧ", "1980-01-02", "2025-06-01"),
)


def _inclusion_dates_of(session_factory: sessionmaker[Session]) -> dict[str, date | None]:
    with session_factory() as session:
        return {
            name: (inclusion_date.date() if inclusion_date else None)
            for name, inclusion_date in session.execute(
                select(
                    RosfinmonitoringEntryRecord.full_name,
                    RosfinmonitoringEntryRecord.inclusion_date,
                )
            ).all()
        }


def test_a_new_snapshot_is_given_the_days_of_inclusion(
    session_factory: sessionmaker[Session],
) -> None:
    """The state publishes no dates, so without this the column stands empty and the
    filter has nothing to filter by. They are read as part of importing a snapshot, not
    as a separate job nobody runs."""
    _entities(session_factory, "Абдулла Гасанович Абабакаров")

    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(*PUBLISHED_INCLUSION),
    ).run()

    dates = _inclusion_dates_of(session_factory)
    assert dates["АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ"] == date(2024, 3, 14)


def test_the_days_survive_a_list_that_did_not_change(
    session_factory: sessionmaker[Session],
) -> None:
    """A second check of an unchanged page imports no snapshot, and a day already written
    is not re-decided — the file is somebody else's build and can be rebuilt differently."""
    _entities(session_factory, "Абдулла Гасанович Абабакаров")
    first = EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(*PUBLISHED_INCLUSION),
    ).run()
    moved = (
        ("АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ", "1996-06-08", "2019-01-01"),
        ("ИВАНОВ ИВАН ИВАНОВИЧ", "1980-01-02", "2025-06-01"),
    )

    again = EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(*moved),
    ).run()

    assert first.new_snapshot and not again.new_snapshot
    assert _inclusion_dates_of(session_factory)["АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ"] == date(2024, 3, 14)


def _extra_snapshot(
    session_factory: sessionmaker[Session], *, dated: bool, birth: datetime
) -> None:
    """A snapshot holding one entry of our own, with or without a day of inclusion.

    A snapshot the check did not import, so the carrying code can be exercised against a
    previous one without a second download.
    """
    with session_factory.begin() as session:
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=datetime.now(UTC),
            source_url="https://www.fedsfm.ru/documents/terrorists-catalog-portal-act",
            content_hash=f"earlier-{session.scalar(select(func.count()).select_from(RosfinmonitoringSnapshotRecord))}",
            entry_count=1,
            fetched_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.flush()
        session.add(
            RosfinmonitoringEntryRecord(
                snapshot_id=snapshot.id,
                full_name="АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ",
                normalized_name="абабакаров абдулла гасанович",
                matching_key="абабакаровабдуллагасанович",
                birth_date=birth,
                inclusion_date=datetime(2024, 3, 14, tzinfo=UTC) if dated else None,
                raw_data={},
            )
        )


def _days_of_latest(session_factory: sessionmaker[Session]) -> dict[tuple[str, str], object]:
    """(имя, дата рождения) → день включения, for the newest snapshot only."""
    latest = SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory).latest_imported_snapshot()
    assert latest is not None
    with session_factory() as session:
        return {
            (full_name, birth_date.date().isoformat() if birth_date else ""): inclusion_date
            for full_name, birth_date, inclusion_date in session.execute(
                select(
                    RosfinmonitoringEntryRecord.full_name,
                    RosfinmonitoringEntryRecord.birth_date,
                    RosfinmonitoringEntryRecord.inclusion_date,
                ).where(RosfinmonitoringEntryRecord.snapshot_id == latest.snapshot_id),
            ).all()
        }


def test_a_new_snapshot_takes_the_day_from_the_previous_one_when_the_file_is_unreadable(
    session_factory: sessionmaker[Session],
) -> None:
    """The gap this closes: a snapshot imported on a day the ОВД-Инфо file could not be
    read arrives with no days at all, and the page that filters by them stays empty until
    the state next changes the list — which may be weeks. The previous snapshot is our own
    record of the same list a moment ago, and it knows the day."""
    _entities(session_factory, "Абдулла Гасанович Абабакаров")
    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(*PUBLISHED_INCLUSION),
    ).run()
    _extra_snapshot(session_factory, dated=True, birth=datetime(1996, 6, 8, tzinfo=UTC))

    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(unreadable=True),
    ).run()

    days = _days_of_latest(session_factory)
    assert days[("АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ", "1996-06-08")] is not None, (
        "the new snapshot must not stand empty while the day one snapshot back is known"
    )


def test_a_snapshot_imported_before_the_parser_was_fixed_recovers_its_birth_dates(
    session_factory: sessionmaker[Session],
) -> None:
    """The gap this closes: a snapshot imported before the parser understood the former
    names in brackets has 795 entries with no date of birth, and those can never be told
    from a namesake. The line to re-read is kept in `raw_data`, so nothing is fetched.

    Stands as the production snapshot does: an entry stored the way the old parser left
    it — the whole line in `raw_data`, no birth date, the brackets and the day inside the
    birth place.
    """
    from db.orm_models import RosfinmonitoringEntryRecord, RosfinmonitoringSnapshotRecord

    # A check of the published page first, so that the snapshot we build below is the one
    # the filling reaches: an unchanged page imports nothing.
    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(*PUBLISHED_INCLUSION),
    ).run()
    line = (
        "96. АБДУКАРИМОВ МАГОМЕД МАГОМЕДОВИЧ*, (АБДУКЕРИМОВ МАГОМЕД МАГОМЕДОВИЧ; "
        "АБДУЛКЕРИМОВ МАГОМЕД МАГОМЕДОВИЧ), 03.05.1964 г.р. , С. ЭЧЕДА;"
    )
    with session_factory.begin() as session:
        snapshot = RosfinmonitoringSnapshotRecord(
            # Later than the check's own, so that this is the snapshot in use and the one
            # the filling is about.
            snapshot_date=datetime.now(UTC) + timedelta(seconds=1),
            source_url="https://www.fedsfm.ru/documents/terrorists-catalog-portal-act",
            content_hash="old-parser",
            entry_count=1,
            fetched_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.flush()
        session.add(
            RosfinmonitoringEntryRecord(
                snapshot_id=snapshot.id,
                full_name="АБДУКАРИМОВ МАГОМЕД МАГОМЕДОВИЧ",
                normalized_name="абдукаримов магомед магомедович",
                matching_key="абдукаримовмагомедмагомедович",
                birth_place="(АБДУКЕРИМОВ МАГОМЕД МАГОМЕДОВИЧ; "
                "АБДУЛКЕРИМОВ МАГОМЕД МАГОМЕДОВИЧ), 03.05.1964 г.р. , С. ЭЧЕДА",
                raw_data={"entry": line},
            )
        )
        older = session.scalar(select(func.max(RosfinmonitoringSnapshotRecord.id)))
    assert older is not None

    # A check of an unchanged page imports nothing, so the snapshot we built is the latest
    # and the filling is the only thing that can reach it.
    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(*PUBLISHED_INCLUSION),
    ).run()

    with session_factory() as session:
        record = session.execute(
            select(
                RosfinmonitoringEntryRecord.birth_date,
                RosfinmonitoringEntryRecord.birth_place,
                RosfinmonitoringEntryRecord.inclusion_date,
                RosfinmonitoringEntryRecord.raw_data,
            ).where(RosfinmonitoringEntryRecord.snapshot_id == older)
        ).one()
    assert record[0] is not None, "the date of birth was in the line all along"
    assert record[1] == "С. ЭЧЕДА", "the place is what follows the date, not the brackets"
    assert record[0].date() == date(1964, 5, 3)
    assert record[3]["former_names"] == [
        "АБДУКЕРИМОВ МАГОМЕД МАГОМЕДОВИЧ",
        "АБДУЛКЕРИМОВ МАГОМЕД МАГОМЕДОВИЧ",
    ], "our own record of the name the entry was published under"


def test_a_recovered_birth_date_does_not_change_who_the_entry_is(
    session_factory: sessionmaker[Session],
) -> None:
    """A date of birth is not who an entry is about. Filling it must leave the name, the
    normalized name and the matching key exactly as the list published them."""
    from db.orm_models import RosfinmonitoringEntryRecord, RosfinmonitoringSnapshotRecord

    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(*PUBLISHED_INCLUSION),
    ).run()
    with session_factory.begin() as session:
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=datetime.now(UTC) + timedelta(seconds=1),
            source_url="https://www.fedsfm.ru/documents/terrorists-catalog-portal-act",
            content_hash="old-parser-2",
            entry_count=1,
            fetched_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.flush()
        session.add(
            RosfinmonitoringEntryRecord(
                snapshot_id=snapshot.id,
                full_name="АБДУКАРОВ АХМЕД ГАСАНОВИЧ",
                normalized_name="абдукаров ахмед гасанович",
                matching_key="абдукаровахмедгасанович",
                raw_data={
                    "entry": "7. АБДУКАРОВ АХМЕД ГАСАНОВИЧ*, (ПРЕЖНИЙ), 11.03.1975 г.р. , Г. МАХАЧКАЛА;"
                },
            )
        )
        older = session.scalar(select(func.max(RosfinmonitoringSnapshotRecord.id)))

    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(*PUBLISHED_INCLUSION),
    ).run()

    with session_factory() as session:
        row = session.execute(
            select(
                RosfinmonitoringEntryRecord.full_name,
                RosfinmonitoringEntryRecord.normalized_name,
                RosfinmonitoringEntryRecord.matching_key,
                RosfinmonitoringEntryRecord.birth_date,
            ).where(RosfinmonitoringEntryRecord.snapshot_id == older)
        ).one()
    assert row[0] == "АБДУКАРОВ АХМЕД ГАСАНОВИЧ"
    assert row[1] == "абдукаров ахмед гасанович"
    assert row[2] == "абдукаровахмедгасанович"
    assert row[3] is not None and row[3].date() == date(1975, 3, 11)


def test_an_entry_with_no_line_to_re_read_is_left_alone(
    session_factory: sessionmaker[Session],
) -> None:
    """A row with nothing in `raw_data` — an Airtable-sourced one, or a row from before
    it was kept — has nothing to re-read, and is left as it is rather than guessed at."""
    from db.orm_models import RosfinmonitoringEntryRecord, RosfinmonitoringSnapshotRecord

    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(*PUBLISHED_INCLUSION),
    ).run()
    with session_factory.begin() as session:
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=datetime.now(UTC) + timedelta(seconds=1),
            source_url="airtable://rfm_persons",
            content_hash="no-raw",
            entry_count=1,
            fetched_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.flush()
        session.add(
            RosfinmonitoringEntryRecord(
                snapshot_id=snapshot.id,
                full_name="ИВАНОВ ИВАН ИВАНОВИЧ",
                normalized_name="иванов иван иванович",
                matching_key="ивановиваниванович",
                raw_data={},
            )
        )
        older = session.scalar(select(func.max(RosfinmonitoringSnapshotRecord.id)))

    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(*PUBLISHED_INCLUSION),
    ).run()

    with session_factory() as session:
        assert (
            session.execute(
                select(RosfinmonitoringEntryRecord.birth_date).where(
                    RosfinmonitoringEntryRecord.snapshot_id == older
                )
            ).scalar_one_or_none()
            is None
        )


def test_a_recovered_birth_date_can_still_reach_the_day_it_had_before(
    session_factory: sessionmaker[Session],
) -> None:
    """The order the two steps run in, and why it is not a matter of taste.

    An entry whose date of birth was just re-read from `raw_data` matches on a pair, and
    the carrying query skips entries that have no birth date — so if the previous
    snapshot is consulted first, those entries never meet it and their day is lost
    whenever the file cannot be read. 825 entries on a snapshot imported before the parser
    was fixed are in exactly that position.
    """
    from db.orm_models import RosfinmonitoringEntryRecord, RosfinmonitoringSnapshotRecord

    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(unreadable=True),
    ).run()
    with session_factory.begin() as session:
        count = session.scalar(select(func.count()).select_from(RosfinmonitoringSnapshotRecord))
        # The previous snapshot: it knows the day of the pair.
        earlier = RosfinmonitoringSnapshotRecord(
            snapshot_date=datetime.now(UTC),
            source_url="https://www.fedsfm.ru/documents/terrorists-catalog-portal-act",
            content_hash=f"earlier-{count}",
            entry_count=1,
            fetched_at=datetime.now(UTC),
        )
        session.add(earlier)
        session.flush()
        session.add(
            RosfinmonitoringEntryRecord(
                snapshot_id=earlier.id,
                full_name="АБАБАКАРОВ АХМЕД ГАСАНОВИЧ",
                normalized_name="абабакаров ахмед гасанович",
                matching_key="абабакаровахмедгасанович",
                birth_date=datetime(1975, 3, 11, tzinfo=UTC),
                inclusion_date=datetime(2014, 1, 8, tzinfo=UTC),
                raw_data={},
            )
        )
        # The snapshot in use: the same entry the way the old parser left it — no date of
        # birth, the whole line in `raw_data`. A separate snapshot, or the day being looked
        # for is the one the test itself put there.
        current = RosfinmonitoringSnapshotRecord(
            snapshot_date=datetime.now(UTC) + timedelta(seconds=1),
            source_url="https://www.fedsfm.ru/documents/terrorists-catalog-portal-act",
            content_hash=f"current-{count}",
            entry_count=1,
            fetched_at=datetime.now(UTC),
        )
        session.add(current)
        session.flush()
        session.add(
            RosfinmonitoringEntryRecord(
                snapshot_id=current.id,
                full_name="АБАБАКАРОВ АХМЕД ГАСАНОВИЧ",
                normalized_name="абабакаров ахмед гасанович",
                matching_key="абабакаровахмедгасанович",
                raw_data={
                    "entry": "7. АБАБАКАРОВ АХМЕД ГАСАНОВИЧ*, (ПРЕЖНИЙ), "
                    "11.03.1975 г.р. , Г. МАХАЧКАЛА;"
                },
            )
        )

    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(unreadable=True),
    ).run()

    with session_factory() as session:
        row = session.execute(
            select(
                RosfinmonitoringEntryRecord.birth_date, RosfinmonitoringEntryRecord.inclusion_date
            ).where(
                RosfinmonitoringEntryRecord.snapshot_id == current.id,
                RosfinmonitoringEntryRecord.full_name == "АБАБАКАРОВ АХМЕД ГАСАНОВИЧ",
            )
        ).one()
    assert row[0] is not None, "the date of birth was in the line all along"
    assert row[1] is not None, (
        "the day was known a snapshot ago and the file is unreadable: the birth date is "
        "recovered first, so the entry must still meet the previous snapshot"
    )
    assert row[1].date() == date(2014, 1, 8)


def test_the_previous_snapshot_gives_nothing_when_it_has_no_day(
    session_factory: sessionmaker[Session],
) -> None:
    """A day is carried, never assumed: a previous snapshot with no days of its own tells
    us nothing, and the file is asked instead."""
    _entities(session_factory, "Абдулла Гасанович Абабакаров")
    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(unreadable=True),
    ).run()
    _extra_snapshot(session_factory, dated=False, birth=datetime(1996, 6, 8, tzinfo=UTC))

    result = EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(*PUBLISHED_INCLUSION),
    ).run()

    assert result.snapshot_id is not None
    assert _inclusion_dates_of(session_factory)["АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ"] == date(
        2024, 3, 14
    ), "nothing to carry, so the file's day must be used"


def test_a_day_is_carried_only_where_the_birth_date_agrees(
    session_factory: sessionmaker[Session],
) -> None:
    """The carried day belongs to a pair, and a name is not a pair: the same name born on
    another day is somebody else and must not inherit it.

    The page changes between the runs, so a new snapshot is really imported — a check of
    an unchanged page imports nothing, and then the snapshot on top is the one the test
    built itself, which would make the case pass for the wrong reason."""
    listed = """
    <li>1. АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ*, 08.06.1996 г.р. , П. МАМЕДКАЛА;</li>
    <li>2. АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ*, 01.01.1970 г.р. , Г. МОСКВА;</li>
    """
    _entities(session_factory, "Абдулла Гасанович Абабакаров")
    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(unreadable=True),
    ).run()
    _extra_snapshot(session_factory, dated=True, birth=datetime(1996, 6, 8, tzinfo=UTC))

    result = EntityRfCheck(
        session_factory,
        download=lambda: _page(listed),
        inclusion_dates=_inclusion_dates(unreadable=True),
    ).run()

    assert result.new_snapshot, "the page changed, so a new snapshot must exist"
    days = _days_of_latest(session_factory)
    assert days[("АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ", "1996-06-08")] is not None, (
        "the pair that was there a snapshot ago keeps its day"
    )
    assert days[("АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ", "1970-01-01")] is None, (
        "a namesake's row must not inherit the other one's day"
    )


def test_a_file_that_cannot_be_read_costs_the_list_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    """The ОВД-Инфо file is a build artefact of someone else's site and can stop being
    readable at any time. The snapshot is already in and the comparison is what this
    check is for; the days are a second thing, and losing them is not a reason to lose
    the first."""
    _entities(session_factory, "Абдулла Гасанович Абабакаров")

    result = EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(unreadable=True),
    ).run()

    assert result.snapshot_id is not None, "the snapshot must stand"
    assert result.new_snapshot is True
    assert result.download_error is None, "the state's own list was read fine"
    assert result.full == 1, "the comparison still ran"
    assert all(day is None for day in _inclusion_dates_of(session_factory).values())


def test_an_entry_the_file_does_not_cover_gets_no_day(
    session_factory: sessionmaker[Session],
) -> None:
    """The list's own second entry has no birth date, so nothing can be matched on both
    sides. ИВАНОВ ИВАН ИВАНОВИЧ in the file is a different person with a birth date, and
    a name that is unique is still only a name."""
    _entities(session_factory, "Абдулла Гасанович Абабакаров")

    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(
            ("АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ", "1996-06-08", "2024-03-14")
        ),
    ).run()

    assert _inclusion_dates_of(session_factory)["ИВАНОВ ИВАН ИВАНОВИЧ"] is None


PEOPLE = (
    "Абдулла Гасанович Абабакаров",  # the list's full name
    "Абдулла Абабакаров",  # no patronymic: maybe a namesake
    "Иван Петрович Иванов",  # another patronymic: another person
    "Иван Иванов",  # its one full name is Иван Петрович: the check merges them
    "Пётр Сидоров",  # not on the list
    "Соломатин П.",  # an initial names nobody for certain
)


def test_a_fresh_list_becomes_a_snapshot_and_the_entities_are_matched_by_name(
    session_factory: sessionmaker[Session],
) -> None:
    _entities(session_factory, *PEOPLE)

    first = EntityRfCheck(session_factory, download=_page).run()
    again = EntityRfCheck(session_factory, download=_page).run()

    assert (first.new_snapshot, first.full, first.possible, first.entries) == (True, 1, 2, 2)
    assert (first.download_error, first.merged, first.merged_region) == (None, 1, 1)
    # The same list is no new snapshot; the matches are rewritten, not added. The first
    # check took «Абдулла Абабакаров» for the listed one and «Иван Иванов» for Иван
    # Петрович, whose patronymic is not the list's.
    assert (again.new_snapshot, again.full, again.possible, again.merged) == (False, 1, 0, 0)
    assert again.download_error is None
    assert _snapshots(session_factory) == 1
    assert _levels(session_factory) == {"Абдулла Гасанович Абабакаров": ["full"]}


def test_a_changed_list_is_a_new_snapshot_and_the_check_uses_it(
    session_factory: sessionmaker[Session],
) -> None:
    _entities(session_factory, *PEOPLE)
    EntityRfCheck(session_factory, download=_page).run()

    changed = PERSONS + "<li>3. СИДОРОВ ПЕТР НИКОЛАЕВИЧ*, 01.01.1990 г.р. , Г. ТВЕРЬ;</li>"
    result = EntityRfCheck(session_factory, download=lambda: _page(changed)).run()

    assert (result.new_snapshot, result.entries, result.possible) == (True, 3, 1)
    assert _snapshots(session_factory) == 2
    assert _levels(session_factory)["Пётр Сидоров"] == ["name"]


def test_an_unreachable_site_leaves_the_last_snapshot_in_use(
    session_factory: sessionmaker[Session],
) -> None:
    _entities(session_factory, *PEOPLE)
    EntityRfCheck(session_factory, download=_page).run()

    def down() -> bytes:
        raise httpx.ConnectError("fedsfm.ru is down")

    result = EntityRfCheck(session_factory, download=down).run()

    assert result.download_error == "ConnectError: fedsfm.ru is down"
    assert (result.new_snapshot, result.full, result.possible) == (False, 1, 0)


def test_no_list_at_all_checks_nothing(session_factory: sessionmaker[Session]) -> None:
    _entities(session_factory, *PEOPLE)

    def down() -> bytes:
        raise httpx.ConnectError("offline")

    result = EntityRfCheck(session_factory, download=down).run()

    assert result.snapshot_id is None and result.download_error is not None
    assert _levels(session_factory) == {}


@pytest.mark.parametrize(
    ("entity", "entry", "level"),
    [
        ("гасанович", "гасанович", "full"),
        ("петрович", "иванович", None),
        (None, "иванович", "name"),
        ("петрович", None, "name"),
        (None, None, "name"),
    ],
)
def test_the_patronymic_tells_a_namesake(
    entity: str | None, entry: str | None, level: str | None
) -> None:
    assert match_level(entity, entry) == level


def test_a_disputed_pair_with_one_side_on_the_list_is_one_person_without_asking(
    session_factory: sessionmaker[Session],
) -> None:
    _entities(
        session_factory,
        "Абдулла Абабакаров",
        # Its only pair with a listed side: taken for one person.
        "Абдулла Гасанович Абабакаров",
        "Иван Иванов",
        # Two listed Ivanovs: the bare one could be either, a person decides.
        "Иван Иванович Иванов",
        "Иван Петрович Иванов",
    )
    listed = PERSONS + "<li>3. ИВАНОВ ИВАН ПЕТРОВИЧ*, , ;</li>"

    result = EntityRfCheck(session_factory, download=lambda: _page(listed)).run()

    assert result.merged == 1
    with session_factory() as session:
        names = set(session.scalars(select(EntityGroupRecord.name)))
        decisions = session.execute(
            select(
                EntityPairDecisionRecord.key_a,
                EntityPairDecisionRecord.key_b,
                EntityPairDecisionRecord.decision,
                EntityPairDecisionRecord.source,
            )
        ).all()
    assert "Абдулла Абабакаров" not in names and "Абдулла Гасанович Абабакаров" in names
    assert {"Иван Иванов", "Иван Иванович Иванов", "Иван Петрович Иванов"} <= names
    assert decisions == [("абдулла абабакаров", "абдулла гасанович абабакаров", "same", "rf")]


def test_a_name_without_patronymic_and_its_one_full_name_are_one_person(
    session_factory: sessionmaker[Session],
) -> None:
    """Off the list too: the one full name (one card region) the news name can be."""
    _entities(
        session_factory,
        "Мария Пономаренко",
        "Мария Николаевна Пономаренко",
        # One surname, two forms of a name, off the list: a person decides.
        "Лида Мониава",
        "Лидия Мониава",
    )

    result = EntityRfCheck(session_factory, download=_page).run()

    assert (result.merged, result.merged_region) == (0, 1)
    with session_factory() as session:
        assert set(session.scalars(select(EntityGroupRecord.name))) == {
            "Мария Николаевна Пономаренко",
            "Лида Мониава",
            "Лидия Мониава",
        }
        assert session.scalar(select(EntityPairDecisionRecord.source)) == "region"


def test_spelling_variants_of_one_name_match(session_factory: sessionmaker[Session]) -> None:
    """The list writes «ВАЛЕРИЕВНА», the news «Валерьевна»; «НАТАЛИЯ» and «Наталья»."""
    _entities(session_factory, "Виолетта Валерьевна Веригина", "Наталья Евгеньевна Шульга")
    listed = """
    <li>1. ВЕРИГИНА ВИОЛЕТТА ВАЛЕРИЕВНА*, 01.01.1990 г.р. , Г. ТВЕРЬ;</li>
    <li>2. ШУЛЬГА НАТАЛИЯ ЕВГЕНИЕВНА*, 01.01.1980 г.р. , Г. КИЕВ;</li>
    """

    result = EntityRfCheck(session_factory, download=lambda: _page(listed)).run()

    assert result.full == 2


def test_a_download_failure_of_our_own_also_leaves_the_last_snapshot_in_use(
    session_factory: sessionmaker[Session],
) -> None:
    """The site answering 403, or serving a page that is not the list, is a failure of
    the same kind as the site being down.

    It was not: the downloader raised its own error while this still caught
    `httpx.HTTPError`, so the exception passed through and the stage died instead of
    falling back to the last snapshot — which is the whole promise of the function.
    """
    from rosfinmonitoring.download import RosfinmonitoringDownloadError

    _entities(session_factory, *PEOPLE)
    EntityRfCheck(session_factory, download=_page).run()

    def refused() -> bytes:
        raise RosfinmonitoringDownloadError("fedsfm.ru вернул 403")

    result = EntityRfCheck(session_factory, download=refused).run()

    assert result.download_error == "RosfinmonitoringDownloadError: fedsfm.ru вернул 403"
    assert (result.new_snapshot, result.full) == (False, 1)


def _table_row(added: str = "2023-11-20") -> OperatorRow:
    return OperatorRow(
        "АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ",
        date(1996, 6, 8),
        "П. МАМЕДКАЛА",
        date.fromisoformat(added),
        False,
        "терроризм",
        "",
    )


def _sources_of(session_factory: sessionmaker[Session]) -> dict[str, str | None]:
    with session_factory() as session:
        return dict(
            session.execute(
                select(
                    RosfinmonitoringEntryRecord.full_name,
                    RosfinmonitoringEntryRecord.inclusion_source,
                )
            ).all()
        )


@pytest.mark.parametrize("copy_unreadable", [False, True])
def test_the_operator_s_table_dates_what_the_ovd_info_copy_does_not(
    session_factory: sessionmaker[Session], copy_unreadable: bool
) -> None:
    """Read in the same check, after the copy — and also when the copy cannot be read at
    all: the table is another source, and one failing is no reason to skip the other."""
    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(unreadable=copy_unreadable),
        operator_table=lambda: [_table_row()],
    ).run()

    assert _inclusion_dates_of(session_factory)["АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ"] == date(
        2023, 11, 20
    )
    assert _sources_of(session_factory)["АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ"] == "operator"


def test_the_operator_s_table_does_not_move_a_day_the_copy_gave(
    session_factory: sessionmaker[Session],
) -> None:
    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(*PUBLISHED_INCLUSION),
        operator_table=lambda: [_table_row()],
    ).run()

    assert _inclusion_dates_of(session_factory)["АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ"] == date(2024, 3, 14)
    assert _sources_of(session_factory)["АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ"] is None


def test_a_table_that_cannot_be_read_stops_nothing_and_keeps_the_copy_held(
    session_factory: sessionmaker[Session],
) -> None:
    EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(),
        operator_table=lambda: [_table_row()],
    ).run()

    def refuses() -> list[OperatorRow]:
        raise OperatorTableUnavailable("ссылка отозвана")

    result = EntityRfCheck(
        session_factory,
        download=_page,
        inclusion_dates=_inclusion_dates(),
        operator_table=refuses,
    ).run()

    assert result.snapshot_id is not None
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RfmOperatorEntryRecord)) == 1
    assert _sources_of(session_factory)["АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ"] == "operator"

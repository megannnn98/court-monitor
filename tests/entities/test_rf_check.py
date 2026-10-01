"""The person entities checked against a fresh Rosfinmonitoring list, on PostgreSQL."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    EntityPairDecisionRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from entities.rf_check import EntityRfCheck, match_level
from rosfinmonitoring.inclusion_dates import (
    InclusionDates,
    InclusionDatesUnavailable,
    normalize_name,
)

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

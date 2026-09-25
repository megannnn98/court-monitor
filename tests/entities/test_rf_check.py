"""The person entities checked against a fresh Rosfinmonitoring list, on PostgreSQL."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    RosfinmonitoringSnapshotRecord,
)
from entities.rf_check import EntityRfCheck, match_level

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


PEOPLE = (
    "Абдулла Гасанович Абабакаров",  # the list's full name
    "Абдулла Абабакаров",  # no patronymic: maybe a namesake
    "Иван Петрович Иванов",  # another patronymic: another person
    "Иван Иванов",
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
    assert first.download_error is None
    # The same list is no new snapshot; the matches are rewritten, not added.
    assert (again.new_snapshot, again.full, again.possible) == (False, 1, 2)
    assert again.download_error is None
    assert _snapshots(session_factory) == 1
    assert _levels(session_factory) == {
        "Абдулла Гасанович Абабакаров": ["full"],
        "Абдулла Абабакаров": ["name"],
        "Иван Иванов": ["name"],
    }


def test_a_changed_list_is_a_new_snapshot_and_the_check_uses_it(
    session_factory: sessionmaker[Session],
) -> None:
    _entities(session_factory, *PEOPLE)
    EntityRfCheck(session_factory, download=_page).run()

    changed = PERSONS + "<li>3. СИДОРОВ ПЕТР НИКОЛАЕВИЧ*, 01.01.1990 г.р. , Г. ТВЕРЬ;</li>"
    result = EntityRfCheck(session_factory, download=lambda: _page(changed)).run()

    assert (result.new_snapshot, result.entries, result.possible) == (True, 3, 3)
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
    assert (result.new_snapshot, result.full, result.possible) == (False, 1, 2)


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

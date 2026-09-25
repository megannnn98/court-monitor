"""The person entities checked against a fresh Rosfinmonitoring list, on PostgreSQL."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    EntityPairDecisionRecord,
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

"""A person's removal of an entity that is nobody holds and survives a rebuild."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import EntityGroupRecord, EntityMentionRecord, EntityRemovalRecord
from entities.collector import EntityCollector
from entities.grouping import Entity
from entities.removals import removals, remove, restore, without_removed


def test_a_removal_takes_the_entity_out_and_holds_after_the_region_entered_the_key() -> None:
    entities = [
        Entity("дмитрий путин", "Дмитрий Путин", [1], Counter()),
        Entity("иван петрович иванов · москва", "Иван Петрович Иванов", [2], Counter()),
        Entity("петр петров", "Пётр Петров", [3], Counter()),
    ]

    left = without_removed(entities, ["дмитрий путин", "иван петрович иванов", "никто никтов"])

    assert [entity.key for entity in left] == ["петр петров"]


def _two_people(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        _, run = seed.article(
            source, external_id="a", title="a", text="Суд арестовал Дмитрия Путина и Петра Петрова."
        )
        for surface, first, last in (
            ("Дмитрия Путина", "Дмитрий", "Путин"),
            ("Петра Петрова", "Пётр", "Петров"),
        ):
            mention_id = seed.mention(run, surface, person_id=None)
            session.get_one(EntityMentionRecord, mention_id).normalized_data = {
                "first_name": first,
                "last_name": last,
                "patronymic": None,
            }
        seed.event(run, "арестовал", event_type="arrest", event_date=None, links=[])
        session.commit()


def _names(session_factory: sessionmaker[Session]) -> list[str]:
    with session_factory() as session:
        return sorted(session.scalars(select(EntityGroupRecord.name)))


def test_a_removal_survives_a_rebuild_and_keeps_the_mentions(
    session_factory: sessionmaker[Session],
) -> None:
    _two_people(session_factory)
    EntityCollector(session_factory).run()
    assert _names(session_factory) == ["Дмитрий Путин", "Пётр Петров"]
    with session_factory.begin() as session:
        entity = session.scalars(
            select(EntityGroupRecord).where(EntityGroupRecord.name == "Дмитрий Путин")
        ).one()
        remove(session, entity)

    # At once, and after the next rebuild.
    assert _names(session_factory) == ["Пётр Петров"]
    EntityCollector(session_factory).run()

    assert _names(session_factory) == ["Пётр Петров"]
    with session_factory() as session:
        assert [(record.key, record.name) for record in removals(session)] == [
            ("дмитрий путин", "Дмитрий Путин")
        ]
        # The texts' mentions are the publications' own: they stay.
        assert len(session.scalars(select(EntityMentionRecord)).all()) == 2


def test_a_removal_taken_back_returns_the_entity_at_the_next_rebuild(
    session_factory: sessionmaker[Session],
) -> None:
    _two_people(session_factory)
    EntityCollector(session_factory).run()
    with session_factory.begin() as session:
        entity = session.scalars(
            select(EntityGroupRecord).where(EntityGroupRecord.name == "Дмитрий Путин")
        ).one()
        remove(session, entity)

    with session_factory.begin() as session:
        assert restore(session, "дмитрий путин") is True
        assert restore(session, "никто никтов") is False
    assert _names(session_factory) == ["Пётр Петров"]
    EntityCollector(session_factory).run()

    assert _names(session_factory) == ["Дмитрий Путин", "Пётр Петров"]
    with session_factory() as session:
        assert session.scalars(select(EntityRemovalRecord)).all() == []

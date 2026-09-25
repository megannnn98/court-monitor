"""A person's corrections of entity names win and survive a rebuild."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import EntityGroupRecord, EntityMentionRecord
from entities.collector import EntityCollector
from entities.grouping import Entity
from entities.overrides import apply_overrides, clean_name, correct_name


def test_a_correction_names_the_entity_and_holds_after_the_region_entered_the_key() -> None:
    entities = [
        Entity("лида мониава", "Лида Мониава", [1], Counter()),
        Entity("иван петрович иванов · москва", "Иван Петрович Иванов", [2], Counter()),
        Entity("петр петров", "Пётр Петров", [3], Counter()),
    ]

    named = apply_overrides(
        entities,
        {"лида мониава": "Лидия Мониава", "иван петрович иванов": "Иван Петрович Иванов-Сидоров"},
    )

    assert {(entity.name, entity.name_source) for entity in named} == {
        ("Лидия Мониава", "manual"),
        ("Иван Петрович Иванов-Сидоров", "manual"),
        ("Пётр Петров", "rules"),
    }


def test_a_corrected_name_that_is_another_entity_s_is_one_person() -> None:
    """«Женя Беркович» corrected to «Евгения Беркович»: the same person as that one."""
    entities = [
        Entity("евгения беркович", "Евгения Беркович", [1, 2], Counter({"Евгении Беркович": 2})),
        Entity("женя беркович", "Женя Беркович", [3], Counter({"Женя Беркович": 1})),
    ]

    [merged] = apply_overrides(entities, {"женя беркович": "Евгения Беркович"})

    assert (merged.key, sorted(merged.mention_ids)) == ("евгения беркович", [1, 2, 3])
    assert merged.variants == {"Евгении Беркович": 2, "Женя Беркович": 1}


def test_a_name_written_surname_first_is_turned() -> None:
    assert clean_name("  Мониава   Лидия ") == "Лидия Мониава"
    assert clean_name("Лидия Мониава") == "Лидия Мониава"


def test_a_correction_survives_a_rebuild(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        _, run = seed.article(
            source, external_id="a", title="a", text="Суд оштрафовал Лиду Мониаву."
        )
        mention_id = seed.mention(run, "Лиду Мониаву", person_id=None)
        session.get_one(EntityMentionRecord, mention_id).normalized_data = {
            "first_name": "Лида",
            "last_name": "Мониава",
            "patronymic": None,
        }
        seed.event(run, "оштрафовал", event_type="arrest", event_date=None, links=[])
        session.commit()
    EntityCollector(session_factory).run()
    with session_factory.begin() as session:
        entity = session.scalars(select(EntityGroupRecord)).one()
        correct_name(session, entity, "Мониава Лидия")

    EntityCollector(session_factory).run()

    with session_factory() as session:
        entity = session.scalars(select(EntityGroupRecord)).one()
        assert (entity.name, entity.name_source) == ("Лидия Мониава", "manual")

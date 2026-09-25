"""Entities that may be one person: the pairs, and a person's decisions on them."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import (
    EntityGroupMentionRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
    EntityMentionRecord,
)
from entities.collector import EntityCollector
from entities.disputes import (
    DIFFERENT,
    SAME,
    EntityRef,
    decide,
    find_pairs,
    merge_decided,
)
from entities.grouping import Entity


def ref(id_: int, key: str, name: str, mentions: int = 1) -> EntityRef:
    return EntityRef(id=id_, key=key, name=name, mention_count=mentions)


def test_the_pairs_are_a_missing_patronymic_and_a_form_of_a_given_name() -> None:
    entities = [
        ref(1, "игорь ранав", "Игорь Ранав", 13),
        ref(2, "игорь александрович ранав", "Игорь Александрович Ранав", 2),
        ref(3, "лида мониава", "Лида Мониава", 99),
        ref(4, "лидия мониава", "Лидия Мониава", 5),
        # Two genders are two people.
        ref(5, "александр петров", "Александр Петров"),
        ref(6, "александра петров", "Александра Петрова"),
        # So are two patronymics, and two given names far apart.
        ref(7, "данил петрович сидоров", "Данил Петрович Сидоров"),
        ref(8, "данила николаевич сидоров", "Данила Николаевич Сидоров"),
        ref(9, "алексей орлов", "Алексей Орлов"),
        ref(10, "александр орлов", "Александр Орлов"),
        # A form more than two letters longer is not offered.
        ref(11, "лиза смирнова", "Лиза Смирнова"),
        ref(12, "лизавета смирнова", "Лизавета Смирнова"),
    ]

    pairs = find_pairs(entities)

    assert [(pair.kind, pair.left.name, pair.right.name) for pair in pairs] == [
        ("similar", "Лида Мониава", "Лидия Мониава"),
        ("patronymic", "Игорь Ранав", "Игорь Александрович Ранав"),
    ]
    # A decided pair is off the list.
    assert len(find_pairs(entities, [("игорь александрович ранав", "игорь ранав")])) == 1


def test_a_decided_merge_is_repeated_on_the_rebuilt_entities() -> None:
    full = Entity(
        "игорь александрович ранав",
        "Игорь Александрович Ранав",
        [1, 2],
        Counter({"Игорь Александрович Ранав": 2}),
        patronymic="александрович",
        regions=Counter({"Чукотский автономный округ": 1}),
    )
    bare = Entity(
        "игорь ранав", "Игорь Ранав", [3, 4, 5], Counter({"Игорь Ранав": 3}), gender="male"
    )
    other = Entity("иван иванов", "Иван Иванов", [6])

    merged = merge_decided([full, bare, other], [("игорь александрович ранав", "игорь ранав")])

    # The fuller name names it, though the other has more mentions.
    assert [(entity.key, entity.name, sorted(entity.mention_ids)) for entity in merged] == [
        ("игорь александрович ранав", "Игорь Александрович Ранав", [1, 2, 3, 4, 5]),
        ("иван иванов", "Иван Иванов", [6]),
    ]
    assert merged[0].gender == "male"
    assert merged[0].regions == {"Чукотский автономный округ": 1}
    # A key absent from the rebuild merges nothing.
    assert len(merge_decided([other], [("иван иванов", "никто")])) == 1


def _person(
    session: Session,
    seed: ResearchSeeder,
    run: int,
    surface: str,
    first: str,
    last: str,
    patronymic: str | None = None,
) -> int:
    mention_id = seed.mention(run, surface, person_id=None)
    session.get_one(EntityMentionRecord, mention_id).normalized_data = {
        "first_name": first,
        "last_name": last,
        "patronymic": patronymic,
    }
    return mention_id


def _seed(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        card = "Ранав Игорь Александрович.\nРегион: Чукотский автономный округ.\nОсужден."
        _, run = seed.article(source, external_id="card", title="Ранав", text=card)
        _person(session, seed, run, "Ранав Игорь Александрович", "Игорь", "Ранав", "Александрович")
        seed.event(run, "Осужден", event_type="sentence", event_date=None, links=[])
        for external_id in ("a", "b"):
            news = f"Суд арестовал Игоря Ранава ({external_id})."
            _, run = seed.article(source, external_id=external_id, title="Арест", text=news)
            _person(session, seed, run, "Игоря Ранава", "Игорь", "Ранав")
            seed.event(run, "арестовал", event_type="arrest", event_date=None, links=[])
        session.commit()
    EntityCollector(session_factory).run()


def _entities(
    session_factory: sessionmaker[Session],
) -> dict[str, tuple[int, int, list[list[object]]]]:
    with session_factory() as session:
        return {
            entity.name: (entity.mention_count, entity.article_count, entity.regions)
            for entity in session.scalars(select(EntityGroupRecord))
        }


def test_one_person_merges_at_once_keeps_the_stronger_role_and_again_after_a_rebuild(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    with session_factory.begin() as session:
        ids = {entity.key: entity.id for entity in session.scalars(select(EntityGroupRecord))}
        session.add(
            EntityGroupRoleRecord(
                group_id=ids["игорь александрович ранав"],
                role="mentioned",
                method="model",
                reason="—",
                quote="",
            )
        )
        session.add(
            EntityGroupRoleRecord(
                group_id=ids["игорь ранав"],
                role="figurant",
                kind="accused",
                method="model",
                reason="арестован",
                quote="",
            )
        )
    assert set(_entities(session_factory)) == {"Игорь Ранав", "Игорь Александрович Ранав"}

    with session_factory.begin() as session:
        kept = decide(session, "игорь ранав", "игорь александрович ранав", SAME)

    merged = _entities(session_factory)
    assert merged == {"Игорь Александрович Ранав": (3, 3, [["Чукотский автономный округ", 1]])}
    with session_factory() as session:
        assert kept == ids["игорь александрович ранав"]
        assert session.scalar(select(EntityGroupRoleRecord.role)) == "figurant"
        assert set(session.scalars(select(EntityGroupMentionRecord.group_id))) == {kept}

    EntityCollector(session_factory).run()

    assert _entities(session_factory) == merged


def test_different_people_stay_apart(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)

    with session_factory.begin() as session:
        kept = decide(session, "игорь ранав", "игорь александрович ранав", DIFFERENT)

    assert kept is None
    assert set(_entities(session_factory)) == {"Игорь Ранав", "Игорь Александрович Ранав"}

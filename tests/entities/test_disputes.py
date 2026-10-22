"""Entities that may be one person: the pairs, and a person's decisions on them."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from analysis.service import GroupFollowers
from processing.identity.collector import EntityCollector
from processing.identity.disputes import (
    BY_RF,
    DIFFERENT,
    SAME,
    EntityRef,
    _only_respelled,
    decide,
    decided_pairs,
    find_pairs,
    merge_clear_pairs,
    merge_decided,
)
from processing.identity.grouping import Entity
from processing.storage.models import (
    EntityGroupMentionRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
    EntityMentionRecord,
)


def ref(id_: int, key: str, name: str, mentions: int = 1) -> EntityRef:
    return EntityRef(id=id_, key=key, name=name, mention_count=mentions)


def test_a_given_name_spelled_with_one_letter_changed_is_a_pair() -> None:
    entities = [
        ref(1, "эммануэль макрон", "Эммануэль Макрон", 40),
        ref(2, "эмманюэль макрон", "Эмманюэль Макрон", 3),
        # A short name one letter apart is no spelling of the other.
        ref(3, "олег орлов", "Олег Орлов"),
        ref(4, "олаг орлов", "Олаг Орлов"),
        # The old rule still finds a form one letter apart in a short name.
        ref(5, "лидия сидорова", "Лидия Сидорова"),
        ref(6, "лидии сидорова", "Лидии Сидорова"),
    ]

    pairs = find_pairs(entities)

    assert [(pair.kind, pair.left.name, pair.right.name) for pair in pairs] == [
        ("similar", "Эммануэль Макрон", "Эмманюэль Макрон"),
        ("similar", "Лидия Сидорова", "Лидии Сидорова"),
    ]
    # Only the one-letter guess is left to a person; the old rule's pair may be merged.
    assert [_only_respelled(pair) for pair in pairs] == [True, False]


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


RANAV = "игорь александрович ранав · чукотский автономный округ"


def _person(
    session: Session,
    seed: DatabaseSeeder,
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
        seed = DatabaseSeeder(session)
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
    EntityCollector(session_factory, downstream=GroupFollowers()).run()


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
                group_id=ids[RANAV],
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
        kept = decide(session, "игорь ранав", RANAV, SAME, downstream=GroupFollowers())

    merged = _entities(session_factory)
    assert merged == {"Игорь Александрович Ранав": (3, 3, [["Чукотский автономный округ", 1]])}
    with session_factory() as session:
        assert kept == ids[RANAV]
        assert session.scalar(select(EntityGroupRoleRecord.role)) == "figurant"
        assert set(session.scalars(select(EntityGroupMentionRecord.group_id))) == {kept}

    EntityCollector(session_factory, downstream=GroupFollowers()).run()

    assert _entities(session_factory) == merged


def test_different_people_stay_apart(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)

    with session_factory.begin() as session:
        kept = decide(session, "игорь ранав", RANAV, DIFFERENT, downstream=GroupFollowers())

    assert kept is None
    assert set(_entities(session_factory)) == {"Игорь Ранав", "Игорь Александрович Ранав"}


def test_a_full_name_beside_its_cards_of_several_regions_is_a_pair_each() -> None:
    entities = [
        ref(1, "денис владимирович попов", "Денис Владимирович Попов", 6),
        ref(2, "денис владимирович попов · курская область", "Денис Владимирович Попов", 2),
        ref(3, "денис владимирович попов · краснодарский край", "Денис Владимирович Попов", 1),
        # Two regions of one form of a name: two people, no «similar» pair.
        ref(4, "данил петрович сидоров · тверская область", "Данил Петрович Сидоров"),
        ref(5, "данила петрович сидоров · курская область", "Данила Петрович Сидоров"),
    ]

    pairs = find_pairs(entities)

    assert sorted((pair.kind, pair.left.id, pair.right.id) for pair in pairs) == [
        ("region", 1, 2),
        ("region", 1, 3),
    ]


def test_a_decision_made_before_the_region_entered_the_key_still_holds() -> None:
    from processing.identity.disputes import resolve_keys

    decided = [("мария бонцлер", "мария владимировна бонцлер")]
    one_card = ["мария бонцлер", "мария владимировна бонцлер · москва"]
    two_cards = [*one_card, "мария владимировна бонцлер · тверская область"]

    assert resolve_keys(decided, one_card) == [
        ("мария бонцлер", "мария владимировна бонцлер · москва")
    ]
    # Which card it meant is unknown: the decision is left out.
    assert resolve_keys(decided, two_cards) == []
    full = Entity("мария владимировна бонцлер · москва", "Мария Владимировна Бонцлер", [1])
    bare = Entity("мария бонцлер", "Мария Бонцлер", [2, 3])
    assert [entity.key for entity in merge_decided([full, bare], decided)] == [
        "мария владимировна бонцлер · москва"
    ]
    refs = [ref(1, key, key) for key in one_card]
    assert find_pairs(refs, decided) == []


def test_a_decision_holds_when_the_rules_cut_the_surname_another_way() -> None:
    """«навальный» became «навальн» once adjective endings counted."""
    from processing.identity.disputes import resolve_keys

    decided = [("алексей анатольевич навальный · москва", "алексей навальный")]
    today = ["алексей анатольевич навальн · москва", "алексей навальн", "алексей навальн · тверь"]

    assert resolve_keys(decided, today) == [
        ("алексей анатольевич навальн · москва", "алексей навальн")
    ]


def _refs(session: Session) -> list[EntityRef]:
    return [
        EntityRef(id=group.id, key=group.key, name=group.name, mention_count=group.mention_count)
        for group in session.scalars(select(EntityGroupRecord))
    ]


def _group(session: Session, key: str, name: str) -> int:
    group = EntityGroupRecord(
        key=key,
        name=name,
        variants=[],
        mention_count=5,
        article_count=1,
        event_types={},
        regions=[],
    )
    session.add(group)
    session.flush()
    return group.id


def test_a_clear_pair_merges_by_the_list_but_a_one_letter_guess_waits_for_a_person(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        spelled = [
            _group(session, "эммануэль макрон", "Эммануэль Макрон"),
            _group(session, "эмманюэль макрон", "Эмманюэль Макрон"),
        ]
        ending = [
            _group(session, "лида мониава", "Лида Мониава"),
            _group(session, "лидия мониава", "Лидия Мониава"),
        ]

    with session_factory.begin() as session:
        merged = merge_clear_pairs(
            session, listed={spelled[0], ending[0]}, downstream=GroupFollowers()
        )

    # The pair of one ending's difference merged; the guess stays, undecided.
    assert merged == Counter({BY_RF: 1})
    with session_factory() as session:
        keys = set(session.scalars(select(EntityGroupRecord.key)))
        assert {"эммануэль макрон", "эмманюэль макрон"} <= keys
        assert len(keys & {"лида мониава", "лидия мониава"}) == 1
        assert [pair.kind for pair in find_pairs(_refs(session), decided_pairs(session))] == [
            "similar"
        ]

"""The identity service is the one door to entity groups: what it does is what the
modules behind it do, in the caller's transaction."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import EntityGroupRecord, EntityMentionRecord
from processing.identity.service import (
    DIFFERENT,
    MANUAL,
    SAME,
    EntityRef,
    IdentityService,
    identity_service_from_env,
    key_region,
)


def _people(session_factory: sessionmaker[Session], *names: tuple[str, str, str | None]) -> None:
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        for number, (first, last, patronymic) in enumerate(names):
            # One article each: inside one article a name with and without a patronymic
            # are joined by the rules, and nothing is left to dispute.
            _, run = seed.article(
                source,
                external_id=str(number),
                title=str(number),
                text=f"Суд арестовал {first} {last}.",
            )
            mention_id = seed.mention(run, f"{first} {last}", person_id=None)
            session.get_one(EntityMentionRecord, mention_id).normalized_data = {
                "first_name": first,
                "last_name": last,
                "patronymic": patronymic,
            }
            seed.event(run, "арестовал", event_type="arrest", event_date=None, links=[])
        session.commit()


def _refs(session: Session) -> list[EntityRef]:
    return [
        EntityRef(id=group.id, key=group.key, name=group.name, mention_count=group.mention_count)
        for group in session.scalars(select(EntityGroupRecord).order_by(EntityGroupRecord.key))
    ]


def test_without_a_key_the_groups_keep_the_names_the_rules_gave() -> None:
    assert identity_service_from_env({}).names_by_model is False


def test_a_rebuild_makes_one_group_of_a_person(session_factory: sessionmaker[Session]) -> None:
    _people(session_factory, ("Иван", "Сидоров", None), ("Пётр", "Кузнецов", None))

    result = IdentityService().rebuild_groups(session_factory)

    assert (result.mentions, result.entities) == (2, 2)
    with session_factory() as session:
        assert [ref.name for ref in _refs(session)] == ["Иван Сидоров", "Пётр Кузнецов"]


def test_a_pair_is_disputed_until_a_person_decides_it(
    session_factory: sessionmaker[Session],
) -> None:
    _people(session_factory, ("Иван", "Сидоров", None), ("Иван", "Сидоров", "Петрович"))
    identity = IdentityService()
    identity.rebuild_groups(session_factory)

    with session_factory() as session:
        (pair,) = identity.disputed_pairs(session, _refs(session))
        first, second = pair.keys

        assert identity.decide_pair(session, first, second, DIFFERENT) is None
        assert identity.disputed_pairs(session, _refs(session)) == []
        assert identity.decision_counts(session)[MANUAL] == 1

        assert identity.reset_decisions(session) == 1
        assert len(identity.disputed_pairs(session, _refs(session))) == 1


def test_one_person_is_merged_at_once(session_factory: sessionmaker[Session]) -> None:
    _people(session_factory, ("Иван", "Сидоров", None), ("Иван", "Сидоров", "Петрович"))
    identity = IdentityService()
    identity.rebuild_groups(session_factory)

    with session_factory() as session:
        (pair,) = identity.disputed_pairs(session, _refs(session))

        merged = identity.decide_pair(session, *pair.keys, SAME)

        assert merged is not None
        assert [ref.mention_count for ref in _refs(session)] == [2]


def test_a_removed_group_stays_out_until_it_is_restored(
    session_factory: sessionmaker[Session],
) -> None:
    _people(session_factory, ("Иван", "Сидоров", None))
    identity = IdentityService()
    identity.rebuild_groups(session_factory)

    with session_factory() as session:
        group = session.scalars(select(EntityGroupRecord)).one()
        key = group.key
        identity.remove_group(session, group)
        session.commit()
    identity.rebuild_groups(session_factory)
    with session_factory() as session:
        assert _refs(session) == []
        assert [record.key for record in identity.removed_groups(session)] == [key]
        assert identity.restore_group(session, key) is True
        session.commit()
    identity.rebuild_groups(session_factory)
    with session_factory() as session:
        assert [ref.key for ref in _refs(session)] == [key]


def test_a_corrected_name_survives_a_rebuild(session_factory: sessionmaker[Session]) -> None:
    _people(session_factory, ("Иван", "Сидоров", None))
    identity = IdentityService()
    identity.rebuild_groups(session_factory)

    with session_factory() as session:
        identity.correct_name(
            session, session.scalars(select(EntityGroupRecord)).one(), "Иван Сидорин"
        )
        session.commit()
    identity.rebuild_groups(session_factory)

    with session_factory() as session:
        group = session.scalars(select(EntityGroupRecord)).one()
        assert (group.name, group.name_source) == ("Иван Сидорин", MANUAL)


def test_a_key_tells_its_region_only_when_it_carries_one() -> None:
    assert key_region("иван петрович иванов · москва") == "москва"
    assert key_region("иван иванов") is None

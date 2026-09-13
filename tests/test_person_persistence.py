from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orm_models import PersonAliasRecord, PersonMergeRecord, PersonRecord
from person_models import AliasOrigin, MergeStatus, PersonStatus
from person_persistence import SqlAlchemyPersonPersistence


def test_create_person_persists_and_returns_id(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)

    person_id = persistence.create_person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )

    assert person_id > 0
    with session_factory() as session:
        person = session.scalar(select(PersonRecord).where(PersonRecord.id == person_id))
    assert person is not None
    assert person.canonical_name == "Иван Иванов"
    assert person.status == PersonStatus.ACTIVE.value


def test_create_alias_links_to_person(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    person_id = persistence.create_person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )

    alias_id = persistence.create_alias(
        person_id=person_id,
        surface_text="Ивана Иванова",
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        origin=AliasOrigin.EXTRACTION,
        confidence=0.9,
    )

    assert alias_id > 0
    with session_factory() as session:
        alias = session.scalar(select(PersonAliasRecord).where(PersonAliasRecord.id == alias_id))
    assert alias is not None
    assert alias.person_id == person_id
    assert alias.surface_text == "Ивана Иванова"


def test_find_person_by_matching_key_returns_person(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    person_id = persistence.create_person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )

    found_id = persistence.find_person_by_matching_key("иваниванов")

    assert found_id == person_id


def test_find_person_by_matching_key_returns_none_when_not_found(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)

    found_id = persistence.find_person_by_matching_key("несуществующий")

    assert found_id is None


def test_merge_persons_updates_status_and_creates_record(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    source_id = persistence.create_person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )
    target_id = persistence.create_person(
        canonical_name="И. И. Иванов",
        normalized_name="Иван Иванов",
        matching_key="иииванов",
    )

    merge_id = persistence.merge_persons(
        source_person_id=source_id,
        target_person_id=target_id,
        reason="same person",
    )

    assert merge_id > 0
    with session_factory() as session:
        source = session.scalar(select(PersonRecord).where(PersonRecord.id == source_id))
        merge_record = session.scalar(
            select(PersonMergeRecord).where(PersonMergeRecord.id == merge_id)
        )
    assert source is not None
    assert source.status == PersonStatus.MERGED.value
    assert source.merged_into_id == target_id
    assert merge_record is not None
    assert merge_record.status == MergeStatus.APPLIED.value


def test_list_aliases_for_person_returns_all_aliases(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    person_id = persistence.create_person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )
    persistence.create_alias(
        person_id=person_id,
        surface_text="Ивана Иванова",
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        origin=AliasOrigin.EXTRACTION,
        confidence=0.9,
    )
    persistence.create_alias(
        person_id=person_id,
        surface_text="И. Иванов",
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        origin=AliasOrigin.MANUAL,
        confidence=1.0,
    )

    aliases = persistence.list_aliases_for_person(person_id)

    assert len(aliases) == 2
    assert all(alias.person_id == person_id for alias in aliases)


def test_duplicate_alias_raises_error(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    person_id = persistence.create_person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )
    persistence.create_alias(
        person_id=person_id,
        surface_text="Иван Иванов",
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        origin=AliasOrigin.EXTRACTION,
        confidence=0.9,
    )

    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        persistence.create_alias(
            person_id=person_id,
            surface_text="Иван Иванов",
            normalized_text="Иван Иванов",
            matching_key="иваниванов",
            origin=AliasOrigin.MANUAL,
            confidence=1.0,
        )

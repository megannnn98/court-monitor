from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import PersonAliasRecord, PersonRecord
from persons.models import AliasOrigin, ResolutionResult, ResolutionStatus
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolver import RuleBasedPersonResolver


def _create_person_with_alias(
    persistence: SqlAlchemyPersonPersistence,
    *,
    canonical_name: str,
    normalized_name: str,
    matching_key: str,
    alias_surface: str,
) -> int:
    person_id = persistence.create_person(
        canonical_name=canonical_name,
        normalized_name=normalized_name,
        matching_key=matching_key,
    )
    persistence.create_alias(
        person_id=person_id,
        surface_text=alias_surface,
        normalized_text=normalized_name,
        matching_key=matching_key,
        origin=AliasOrigin.EXTRACTION,
        confidence=0.9,
    )
    return person_id


def test_resolver_returns_new_person_when_no_match(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    resolver = RuleBasedPersonResolver(persistence)

    result = resolver.resolve(
        normalized_text="Новый Человек",
        matching_key="новыйчеловек",
        surface_text="Нового Человека",
    )

    assert result.status is ResolutionStatus.NEW_PERSON
    assert result.person_id is None
    assert result.confidence == 1.0


def test_resolver_returns_matched_when_exact_key_match(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    person_id = _create_person_with_alias(
        persistence,
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
        alias_surface="Ивана Иванова",
    )
    resolver = RuleBasedPersonResolver(persistence)

    result = resolver.resolve(
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        surface_text="Ивану Иванову",
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.person_id == person_id
    assert result.confidence == 1.0


def test_resolver_returns_matched_for_alias_match(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    person_id = _create_person_with_alias(
        persistence,
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
        alias_surface="И. Иванов",
    )
    resolver = RuleBasedPersonResolver(persistence)

    result = resolver.resolve(
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        surface_text="И. Иванов",
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.person_id == person_id


def test_resolver_creates_new_person_and_alias(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    resolver = RuleBasedPersonResolver(persistence)

    result = resolver.resolve_and_create(
        normalized_text="Новый Человек",
        matching_key="новыйчеловек",
        surface_text="Нового Человека",
        origin=AliasOrigin.EXTRACTION,
        confidence=0.85,
    )

    assert result.status is ResolutionStatus.NEW_PERSON
    assert result.person_id is not None

    with session_factory() as session:
        person = session.scalar(select(PersonRecord).where(PersonRecord.id == result.person_id))
        aliases = session.scalars(
            select(PersonAliasRecord).where(PersonAliasRecord.person_id == result.person_id)
        ).all()

    assert person is not None
    assert person.canonical_name == "Новый Человек"
    assert len(aliases) == 1
    assert aliases[0].surface_text == "Нового Человека"


def test_resolver_adds_alias_to_existing_person(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    person_id = _create_person_with_alias(
        persistence,
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
        alias_surface="Иван Иванов",
    )
    resolver = RuleBasedPersonResolver(persistence)

    result = resolver.resolve_and_create(
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        surface_text="И. И. Иванов",
        origin=AliasOrigin.EXTRACTION,
        confidence=0.9,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.person_id == person_id

    with session_factory() as session:
        aliases = session.scalars(
            select(PersonAliasRecord).where(PersonAliasRecord.person_id == person_id)
        ).all()

    assert len(aliases) == 2
    surfaces = {alias.surface_text for alias in aliases}
    assert "Иван Иванов" in surfaces
    assert "И. И. Иванов" in surfaces


def test_resolver_does_not_duplicate_alias(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    person_id = _create_person_with_alias(
        persistence,
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
        alias_surface="Иван Иванов",
    )
    resolver = RuleBasedPersonResolver(persistence)

    result1 = resolver.resolve_and_create(
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        surface_text="Иван Иванов",
        origin=AliasOrigin.EXTRACTION,
        confidence=0.9,
    )
    result2 = resolver.resolve_and_create(
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        surface_text="Иван Иванов",
        origin=AliasOrigin.EXTRACTION,
        confidence=0.9,
    )

    assert result1.person_id == result2.person_id

    with session_factory() as session:
        aliases = session.scalars(
            select(PersonAliasRecord).where(PersonAliasRecord.person_id == person_id)
        ).all()

    assert len(aliases) == 1


def test_resolver_recovers_from_concurrent_person_creation(
    session_factory: sessionmaker[Session],
) -> None:
    """A concurrent resolver may create the same new person between this
    resolver's `resolve()` lookup and its own create — simulated here by
    injecting a competing insert right after that lookup reports NEW_PERSON.
    `resolve_and_create` must back off to the winner instead of creating a
    duplicate canonical person or raising.
    """
    persistence = SqlAlchemyPersonPersistence(session_factory)
    resolver = RuleBasedPersonResolver(persistence)
    original_resolve = resolver.resolve

    def resolve_then_lose_race(*args: Any, **kwargs: Any) -> ResolutionResult:
        result = original_resolve(*args, **kwargs)
        if result.status is ResolutionStatus.NEW_PERSON:
            persistence.create_person(
                canonical_name="Иван Иванов",
                normalized_name="Иван Иванов",
                matching_key="иваниванов",
            )
        return result

    resolver.resolve = resolve_then_lose_race  # type: ignore[method-assign]

    result = resolver.resolve_and_create(
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        surface_text="Ивана Иванова",
        origin=AliasOrigin.EXTRACTION,
        confidence=0.9,
    )

    with session_factory() as session:
        persons = session.scalars(
            select(PersonRecord).where(PersonRecord.matching_key == "иваниванов")
        ).all()

    assert len(persons) == 1
    assert result.person_id == persons[0].id
    # Backing off to the winner is a match, not a creation.
    assert result.status is ResolutionStatus.MATCHED

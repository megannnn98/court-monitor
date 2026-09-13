"""Tests for Rosfinmonitoring matcher."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from orm_models import (
    PersonAliasRecord,
    PersonRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from rosfinmonitoring_matcher import RuleBasedRosfinmonitoringMatcher
from rosfinmonitoring_matcher_models import (
    RosfinCandidateEntry,
    RosfinMatchResult,
    RosfinMatchStatus,
)
from rosfinmonitoring_matcher_persistence import RosfinMatchPersistence


@pytest.fixture
def matcher(session_factory: sessionmaker[Session]) -> RuleBasedRosfinmonitoringMatcher:
    """Create a matcher instance."""
    return RuleBasedRosfinmonitoringMatcher(session_factory)


@pytest.fixture
def persistence(session_factory: sessionmaker[Session]) -> RosfinMatchPersistence:
    """Create a persistence instance."""
    return RosfinMatchPersistence(session_factory)


def _create_person(
    session: Session,
    canonical_name: str,
    normalized_name: str,
    matching_key: str,
) -> int:
    """Create a person and return their ID."""
    person = PersonRecord(
        canonical_name=canonical_name,
        normalized_name=normalized_name,
        matching_key=matching_key,
        status="active",
    )
    session.add(person)
    session.commit()
    return person.id


def _create_alias(
    session: Session,
    person_id: int,
    surface_text: str,
    normalized_text: str,
    matching_key: str,
) -> int:
    """Create a person alias and return its ID."""
    alias = PersonAliasRecord(
        person_id=person_id,
        surface_text=surface_text,
        normalized_text=normalized_text,
        matching_key=matching_key,
        origin="extraction",
        confidence=1.0,
    )
    session.add(alias)
    session.commit()
    return alias.id


def _create_snapshot(
    session: Session,
    snapshot_date: datetime,
) -> int:
    """Create a snapshot and return its ID."""
    snapshot = RosfinmonitoringSnapshotRecord(
        snapshot_date=snapshot_date,
        source_url="https://rosfinmonitoring.gov.ru/test",
        content_hash="test_hash_123",
        entry_count=0,
        fetched_at=datetime.now(UTC),
    )
    session.add(snapshot)
    session.commit()
    return snapshot.id


def _create_rf_entry(
    session: Session,
    snapshot_id: int,
    full_name: str,
    normalized_name: str,
    matching_key: str,
    birth_date: datetime | None = None,
) -> int:
    """Create a Rosfinmonitoring entry and return its ID."""
    entry = RosfinmonitoringEntryRecord(
        snapshot_id=snapshot_id,
        full_name=full_name,
        normalized_name=normalized_name,
        matching_key=matching_key,
        birth_date=birth_date,
        status="active",
        raw_data={},
    )
    session.add(entry)
    session.commit()
    return entry.id


def test_match_person_exact_match(
    session_factory: sessionmaker[Session],
    matcher: RuleBasedRosfinmonitoringMatcher,
) -> None:
    """Test matching a person with an exact matching_key match."""
    with session_factory() as session:
        person_id = _create_person(
            session,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )
        _create_alias(
            session,
            person_id,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )

        snapshot_id = _create_snapshot(session, datetime.now(UTC))
        _create_rf_entry(
            session,
            snapshot_id,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )

    result = matcher.match_person(person_id, snapshot_id)

    assert result.person_id == person_id
    assert result.snapshot_id == snapshot_id
    assert result.status == RosfinMatchStatus.MATCHED
    assert result.confidence >= 0.95
    assert result.matched_entry_id is not None
    assert result.matched_entry_name == "Иванов Иван Иванович"
    assert len(result.candidate_entries) >= 1


def test_match_person_no_match(
    session_factory: sessionmaker[Session],
    matcher: RuleBasedRosfinmonitoringMatcher,
) -> None:
    """Test matching a person with no matching Rosfinmonitoring entries."""
    with session_factory() as session:
        person_id = _create_person(
            session,
            "Петров Петр Петрович",
            "петров петр петрович",
            "петровпетрпетрович",
        )
        _create_alias(
            session,
            person_id,
            "Петров Петр Петрович",
            "петров петр петрович",
            "петровпетрпетрович",
        )

        snapshot_id = _create_snapshot(session, datetime.now(UTC))
        _create_rf_entry(
            session,
            snapshot_id,
            "Сидоров Сидор Сидорович",
            "сидоров сидор сидорович",
            "сидоровсидорсидорович",
        )

    result = matcher.match_person(person_id, snapshot_id)

    assert result.person_id == person_id
    assert result.snapshot_id == snapshot_id
    assert result.status == RosfinMatchStatus.NOT_MATCHED
    assert result.confidence == 1.0
    assert result.matched_entry_id is None
    assert len(result.candidate_entries) == 0


def test_match_person_multiple_aliases(
    session_factory: sessionmaker[Session],
    matcher: RuleBasedRosfinmonitoringMatcher,
) -> None:
    """Test matching a person with multiple aliases."""
    with session_factory() as session:
        person_id = _create_person(
            session,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )
        _create_alias(
            session,
            person_id,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )
        _create_alias(
            session,
            person_id,
            "И. И. Иванов",
            "и и иванов",
            "иииванов",
        )

        snapshot_id = _create_snapshot(session, datetime.now(UTC))
        _create_rf_entry(
            session,
            snapshot_id,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )

    result = matcher.match_person(person_id, snapshot_id)

    assert result.status == RosfinMatchStatus.MATCHED
    assert result.confidence >= 0.95


def test_match_person_nonexistent_person(
    matcher: RuleBasedRosfinmonitoringMatcher,
) -> None:
    """Test matching a nonexistent person raises an error."""
    with pytest.raises(ValueError, match="Person 99999 not found"):
        matcher.match_person(99999, 1)


def test_match_all_persons(
    session_factory: sessionmaker[Session],
    matcher: RuleBasedRosfinmonitoringMatcher,
) -> None:
    """Test matching all persons against a snapshot."""
    with session_factory() as session:
        person1_id = _create_person(
            session,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )
        _create_alias(
            session,
            person1_id,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )

        person2_id = _create_person(
            session,
            "Петров Петр Петрович",
            "петров петр петрович",
            "петровпетрпетрович",
        )
        _create_alias(
            session,
            person2_id,
            "Петров Петр Петрович",
            "петров петр петрович",
            "петровпетрпетрович",
        )

        snapshot_id = _create_snapshot(session, datetime.now(UTC))
        _create_rf_entry(
            session,
            snapshot_id,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )

    results = matcher.match_all_persons(snapshot_id)

    assert len(results) == 2

    person1_result = next(r for r in results if r.person_id == person1_id)
    assert person1_result.status == RosfinMatchStatus.MATCHED

    person2_result = next(r for r in results if r.person_id == person2_id)
    assert person2_result.status == RosfinMatchStatus.NOT_MATCHED


def test_persistence_save_and_retrieve(
    session_factory: sessionmaker[Session],
    persistence: RosfinMatchPersistence,
) -> None:
    """Test saving and retrieving a match result."""
    with session_factory() as session:
        person_id = _create_person(
            session,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )
        snapshot_id = _create_snapshot(session, datetime.now(UTC))
        entry_id = _create_rf_entry(
            session,
            snapshot_id,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )

    result = RosfinMatchResult(
        person_id=person_id,
        snapshot_id=snapshot_id,
        status=RosfinMatchStatus.MATCHED,
        confidence=0.98,
        matched_entry_id=entry_id,
        matched_entry_name="Иванов Иван Иванович",
        candidate_entries=[
            RosfinCandidateEntry(
                entry_id=entry_id,
                full_name="Иванов Иван Иванович",
                normalized_name="иванов иван иванович",
                matching_key="ивановиваниванович",
                similarity_score=0.98,
                reasons=["Exact matching_key match"],
            )
        ],
        reasons=["Exact matching_key match"],
        matched_at=datetime.now(UTC),
    )

    match_id = persistence.save_match_result(result)
    assert match_id > 0

    retrieved = persistence.get_match_result(person_id, snapshot_id)
    assert retrieved is not None
    assert retrieved.person_id == person_id
    assert retrieved.snapshot_id == snapshot_id
    assert retrieved.status == RosfinMatchStatus.MATCHED
    assert retrieved.confidence == 0.98
    assert len(retrieved.candidate_entries) == 1


def test_persistence_update_existing(
    session_factory: sessionmaker[Session],
    persistence: RosfinMatchPersistence,
) -> None:
    """Test updating an existing match result."""
    with session_factory() as session:
        person_id = _create_person(
            session,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )
        snapshot_id = _create_snapshot(session, datetime.now(UTC))
        entry_id = _create_rf_entry(
            session,
            snapshot_id,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )

    result1 = RosfinMatchResult(
        person_id=person_id,
        snapshot_id=snapshot_id,
        status=RosfinMatchStatus.NOT_MATCHED,
        confidence=1.0,
        candidate_entries=[],
        reasons=["No match found"],
        matched_at=datetime.now(UTC),
    )

    match_id1 = persistence.save_match_result(result1)

    result2 = RosfinMatchResult(
        person_id=person_id,
        snapshot_id=snapshot_id,
        status=RosfinMatchStatus.MATCHED,
        confidence=0.95,
        matched_entry_id=entry_id,
        matched_entry_name="Иванов Иван Иванович",
        candidate_entries=[
            RosfinCandidateEntry(
                entry_id=entry_id,
                full_name="Иванов Иван Иванович",
                normalized_name="иванов иван иванович",
                matching_key="ивановиваниванович",
                similarity_score=0.95,
                reasons=["Match found"],
            )
        ],
        reasons=["Match found"],
        matched_at=datetime.now(UTC),
    )

    match_id2 = persistence.save_match_result(result2)

    assert match_id1 == match_id2

    retrieved = persistence.get_match_result(person_id, snapshot_id)
    assert retrieved is not None
    assert retrieved.status == RosfinMatchStatus.MATCHED
    assert retrieved.confidence == 0.95


def test_persistence_list_matches_for_snapshot(
    session_factory: sessionmaker[Session],
    persistence: RosfinMatchPersistence,
) -> None:
    """Test listing all matches for a snapshot."""
    with session_factory() as session:
        person1_id = _create_person(
            session,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )
        person2_id = _create_person(
            session,
            "Петров Петр Петрович",
            "петров петр петрович",
            "петровпетрпетрович",
        )
        snapshot_id = _create_snapshot(session, datetime.now(UTC))
        entry_id = _create_rf_entry(
            session,
            snapshot_id,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )

    result1 = RosfinMatchResult(
        person_id=person1_id,
        snapshot_id=snapshot_id,
        status=RosfinMatchStatus.MATCHED,
        confidence=0.95,
        matched_entry_id=entry_id,
        candidate_entries=[],
        reasons=["Match"],
        matched_at=datetime.now(UTC),
    )

    result2 = RosfinMatchResult(
        person_id=person2_id,
        snapshot_id=snapshot_id,
        status=RosfinMatchStatus.NOT_MATCHED,
        confidence=1.0,
        candidate_entries=[],
        reasons=["No match"],
        matched_at=datetime.now(UTC),
    )

    persistence.save_match_result(result1)
    persistence.save_match_result(result2)

    all_matches = persistence.list_matches_for_snapshot(snapshot_id)
    assert len(all_matches) == 2

    matched_only = persistence.list_matches_for_snapshot(
        snapshot_id, status=RosfinMatchStatus.MATCHED
    )
    assert len(matched_only) == 1
    assert matched_only[0].person_id == person1_id


def test_persistence_delete_match(
    session_factory: sessionmaker[Session],
    persistence: RosfinMatchPersistence,
) -> None:
    """Test deleting a match result."""
    with session_factory() as session:
        person_id = _create_person(
            session,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )
        snapshot_id = _create_snapshot(session, datetime.now(UTC))

    result = RosfinMatchResult(
        person_id=person_id,
        snapshot_id=snapshot_id,
        status=RosfinMatchStatus.MATCHED,
        confidence=0.95,
        candidate_entries=[],
        reasons=["Match"],
        matched_at=datetime.now(UTC),
    )

    persistence.save_match_result(result)

    deleted = persistence.delete_match_result(person_id, snapshot_id)
    assert deleted is True

    retrieved = persistence.get_match_result(person_id, snapshot_id)
    assert retrieved is None

    deleted_again = persistence.delete_match_result(person_id, snapshot_id)
    assert deleted_again is False

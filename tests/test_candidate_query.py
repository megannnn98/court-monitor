"""Tests for the main product query service."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from candidate_query_models import RosfinmonitoringStatus
from candidate_query_service import CandidateQueryService
from orm_models import (
    ArticleExtractionRunRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
    PersecutionClassificationRecord,
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonRecord,
    RosfinMatchRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
    Source,
    SourceDocument,
)


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> CandidateQueryService:
    """Create a candidate query service."""
    return CandidateQueryService(session_factory)


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


def _create_persecution_classification(
    session: Session,
    person_id: int,
    status: str,
    confidence: float,
    reasons: list[str],
) -> int:
    """Create a persecution classification and return its ID."""
    classification = PersecutionClassificationRecord(
        person_id=person_id,
        status=status,
        confidence=confidence,
        reasons=reasons,
        evidence_types=[],
        classifier_name="test-classifier",
        classifier_version="1.0.0",
        classified_at=datetime.now(UTC),
    )
    session.add(classification)
    session.commit()
    return classification.id


def _create_snapshot(session: Session) -> int:
    """Create a Rosfinmonitoring snapshot and return its ID."""
    snapshot = RosfinmonitoringSnapshotRecord(
        snapshot_date=datetime.now(UTC),
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
) -> int:
    """Create a Rosfinmonitoring entry and return its ID."""
    entry = RosfinmonitoringEntryRecord(
        snapshot_id=snapshot_id,
        full_name=full_name,
        normalized_name=normalized_name,
        matching_key=matching_key,
        birth_date=None,
        birth_place=None,
        snils=None,
        inn=None,
        inclusion_reason=None,
        inclusion_date=None,
        status="active",
        raw_data={},
    )
    session.add(entry)
    session.commit()
    return entry.id


def _create_match(
    session: Session,
    person_id: int,
    snapshot_id: int,
    status: str,
    confidence: float,
    matched_entry_id: int | None = None,
) -> int:
    """Create a Rosfinmonitoring match and return its ID."""
    match = RosfinMatchRecord(
        person_id=person_id,
        snapshot_id=snapshot_id,
        status=status,
        confidence=confidence,
        matched_entry_id=matched_entry_id,
        matched_entry_name=None,
        candidate_entries={},
        reasons=[],
        matched_at=datetime.now(UTC),
    )
    session.add(match)
    session.commit()
    return match.id


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
        origin="manual",
        confidence=1.0,
    )
    session.add(alias)
    session.commit()
    return alias.id


def test_get_candidates_includes_not_matched_persons(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
) -> None:
    """political + NOT_MATCHED (matching actually ran and found nothing) → included."""
    with session_factory() as session:
        person1_id = _create_person(
            session,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )
        _create_persecution_classification(
            session,
            person1_id,
            status="political",
            confidence=0.9,
            reasons=["Political activity"],
        )
        snapshot_id = _create_snapshot(session)
        _create_match(session, person1_id, snapshot_id, status="not_matched", confidence=0.8)

    result = service.get_candidates(snapshot_id)

    assert result.total_count == 1
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.person_id == person1_id
    assert candidate.rosfinmonitoring_status == RosfinmonitoringStatus.NOT_MATCHED


def test_get_candidates_excludes_persons_never_matched(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
) -> None:
    """political + NO_MATCH_RECORD (matching never ran) → excluded by default.

    No match record is NOT the same as a confirmed absence from the list.
    """
    with session_factory() as session:
        person1_id = _create_person(
            session,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )
        _create_persecution_classification(
            session,
            person1_id,
            status="political",
            confidence=0.9,
            reasons=["Political activity"],
        )
        snapshot_id = _create_snapshot(session)

    result = service.get_candidates(snapshot_id)

    assert result.total_count == 0
    assert result.candidates == []


def test_get_candidates_excludes_needs_review_matches(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
) -> None:
    """political + NEEDS_REVIEW → excluded by default."""
    with session_factory() as session:
        person1_id = _create_person(
            session,
            "Нуждается Вобзоре Тестович",
            "нуждается вобзоре тестович",
            "нуждаетсявобзоретестович",
        )
        _create_persecution_classification(
            session,
            person1_id,
            status="political",
            confidence=0.9,
            reasons=["Political activity"],
        )
        snapshot_id = _create_snapshot(session)
        _create_match(session, person1_id, snapshot_id, status="needs_review", confidence=0.5)

    result = service.get_candidates(snapshot_id)

    assert result.total_count == 0
    assert result.candidates == []


def test_get_candidates_excludes_insufficient_data_matches(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
) -> None:
    """political + INSUFFICIENT_DATA → excluded by default.

    A too-thin name that couldn't be searched reliably is not a confirmed
    absence, same as NO_MATCH_RECORD/AMBIGUOUS/NEEDS_REVIEW.
    """
    with session_factory() as session:
        person1_id = _create_person(
            session,
            "Однословный",
            "однословный",
            "однословный",
        )
        _create_persecution_classification(
            session,
            person1_id,
            status="political",
            confidence=0.9,
            reasons=["Political activity"],
        )
        snapshot_id = _create_snapshot(session)
        _create_match(session, person1_id, snapshot_id, status="insufficient_data", confidence=0.0)

    result = service.get_candidates(snapshot_id)

    assert result.total_count == 0
    assert result.candidates == []


def test_get_candidates_rejects_missing_snapshot(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
) -> None:
    """Test that nonexistent snapshots do not mean "not in RF"."""
    with session_factory() as session:
        person_id = _create_person(
            session,
            "Иванов Иван Иванович",
            "иванов иван иванович",
            "ивановиваниванович",
        )
        _create_persecution_classification(
            session,
            person_id,
            status="political",
            confidence=0.9,
            reasons=["Political activity"],
        )

    with pytest.raises(ValueError, match="Rosfinmonitoring snapshot 999999 not found"):
        service.get_candidates(999999)


def test_get_candidates_excludes_matched_persons(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
) -> None:
    """Test that the query excludes persons matched to RF."""
    with session_factory() as session:
        # Create a politically persecuted person matched to RF
        person1_id = _create_person(
            session,
            "Петров Петр Петрович",
            "петров петр петрович",
            "петровпетрпетрович",
        )
        _create_persecution_classification(
            session,
            person1_id,
            status="political",
            confidence=0.9,
            reasons=["Political activity"],
        )

        # Create a snapshot and RF entry
        snapshot_id = _create_snapshot(session)
        entry_id = _create_rf_entry(
            session,
            snapshot_id,
            "Петров Петр Петрович",
            "петров петр петрович",
            "петровпетрпетрович",
        )

        # Create a match
        _create_match(
            session,
            person1_id,
            snapshot_id,
            status="matched",
            confidence=0.95,
            matched_entry_id=entry_id,
        )

    result = service.get_candidates(snapshot_id)

    assert result.total_count == 0
    assert len(result.candidates) == 0


def test_get_candidates_excludes_ambiguous_matches(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
) -> None:
    """political + AMBIGUOUS → excluded by default (needs manual review, not "absent")."""
    with session_factory() as session:
        person1_id = _create_person(
            session,
            "Сидоров Сидор Сидорович",
            "сидоров сидор сидорович",
            "сидоровсидорович",
        )
        _create_persecution_classification(
            session,
            person1_id,
            status="political",
            confidence=0.85,
            reasons=["Political activity"],
        )
        snapshot_id = _create_snapshot(session)
        _create_match(
            session,
            person1_id,
            snapshot_id,
            status="ambiguous",
            confidence=0.6,
        )

    result = service.get_candidates(snapshot_id)

    assert result.total_count == 0
    assert result.candidates == []


def test_get_candidates_can_widen_included_statuses_for_manual_review(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
) -> None:
    """include_rf_statuses lets a caller opt into reviewing AMBIGUOUS/NEEDS_REVIEW too."""
    with session_factory() as session:
        person1_id = _create_person(
            session,
            "Сидоров Сидор Сидорович",
            "сидоров сидор сидорович",
            "сидоровсидорович",
        )
        _create_persecution_classification(
            session,
            person1_id,
            status="political",
            confidence=0.85,
            reasons=["Political activity"],
        )
        snapshot_id = _create_snapshot(session)
        _create_match(
            session,
            person1_id,
            snapshot_id,
            status="ambiguous",
            confidence=0.6,
        )

    result = service.get_candidates(
        snapshot_id,
        include_rf_statuses=frozenset(
            {RosfinmonitoringStatus.NOT_MATCHED, RosfinmonitoringStatus.AMBIGUOUS}
        ),
    )

    assert result.total_count == 1
    candidate = result.candidates[0]
    assert candidate.rosfinmonitoring_status == RosfinmonitoringStatus.AMBIGUOUS
    assert candidate.rosfinmonitoring_match_confidence == 0.6


def test_get_candidates_respects_confidence_threshold(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
) -> None:
    """Test that the query respects the minimum persecution confidence threshold."""
    with session_factory() as session:
        # Create a person with low persecution confidence
        person1_id = _create_person(
            session,
            "Низкий Порог",
            "низкий порог",
            "низкийпорог",
        )
        _create_persecution_classification(
            session,
            person1_id,
            status="political",
            confidence=0.5,
            reasons=["Low confidence"],
        )

        # Create a person with high persecution confidence
        person2_id = _create_person(
            session,
            "Высокий Порог",
            "высокий порог",
            "высокийпорог",
        )
        _create_persecution_classification(
            session,
            person2_id,
            status="political",
            confidence=0.9,
            reasons=["High confidence"],
        )

        # Create a snapshot; both persons confirmed NOT_MATCHED so this test
        # exercises only the confidence threshold, not RF-status inclusion.
        snapshot_id = _create_snapshot(session)
        _create_match(session, person1_id, snapshot_id, status="not_matched", confidence=0.8)
        _create_match(session, person2_id, snapshot_id, status="not_matched", confidence=0.8)

    # Query with high threshold
    result = service.get_candidates(snapshot_id, min_persecution_confidence=0.7)

    assert result.total_count == 1
    assert result.candidates[0].person_id == person2_id


def test_get_candidates_respects_limit(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
) -> None:
    """Test that the query respects the limit parameter."""
    with session_factory() as session:
        # Create a snapshot up front so each person can get a confirmed
        # NOT_MATCHED record — this test exercises only `limit`.
        snapshot_id = _create_snapshot(session)

        for i in range(5):
            person_id = _create_person(
                session,
                f"Person {i}",
                f"person {i}",
                f"person{i}",
            )
            _create_persecution_classification(
                session,
                person_id,
                status="political",
                confidence=0.9,
                reasons=["Political activity"],
            )
            _create_match(session, person_id, snapshot_id, status="not_matched", confidence=0.8)

    result = service.get_candidates(snapshot_id, limit=3)

    assert result.total_count == 3
    assert len(result.candidates) == 3


def test_get_candidates_includes_event_and_alias_counts(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
) -> None:
    """Test that the query includes event and alias counts."""
    with session_factory() as session:
        # Create a person
        person_id = _create_person(
            session,
            "Test Person",
            "test person",
            "testperson",
        )
        _create_persecution_classification(
            session,
            person_id,
            status="political",
            confidence=0.9,
            reasons=["Political activity"],
        )

        # Create aliases
        _create_alias(session, person_id, "Test Person", "test person", "testperson")
        _create_alias(session, person_id, "T. Person", "t person", "tperson")

        # Create a snapshot with a confirmed NOT_MATCHED record
        snapshot_id = _create_snapshot(session)
        _create_match(session, person_id, snapshot_id, status="not_matched", confidence=0.8)

    result = service.get_candidates(snapshot_id)

    assert result.total_count == 1
    candidate = result.candidates[0]
    assert candidate.alias_count == 2
    assert candidate.event_count == 0  # No events created in this test
    assert candidate.last_event_date is None


def test_get_candidates_uses_event_date_for_last_event_date(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
) -> None:
    """Test that last_event_date comes from extracted event, not link creation time."""
    event_date = datetime(2020, 1, 2, tzinfo=UTC)
    with session_factory() as session:
        person_id = _create_person(
            session,
            "Event Person",
            "event person",
            "eventperson",
        )
        _create_persecution_classification(
            session,
            person_id,
            status="political",
            confidence=0.9,
            reasons=["Political activity"],
        )
        snapshot_id = _create_snapshot(session)
        _create_match(session, person_id, snapshot_id, status="not_matched", confidence=0.8)

        source = Source(name="test", base_url="https://example.com")
        session.add(source)
        session.flush()
        document = SourceDocument(
            source_id=source.id,
            external_id="event-date",
            canonical_url="https://example.com/event-date",
            fetched_at=datetime.now(UTC),
            content_type="text/html",
            raw_content=b"",
        )
        session.add(document)
        session.flush()
        article = ParsedArticleRecord(
            document_id=document.id,
            title="Test",
            published_at=event_date,
            text="Text",
        )
        session.add(article)
        session.flush()
        run = ArticleExtractionRunRecord(
            article_id=article.id,
            article_content_hash="hash",
            extractor_name="test",
            extractor_version="1",
            normalizer_version="1",
            status="succeeded",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        event = ExtractedEventRecord(
            extraction_run_id=run.id,
            event_type="arrest",
            event_date=event_date,
            start_offset=0,
            end_offset=4,
            confidence=0.9,
            attributes={},
            extractor_name="test",
            extractor_version="1",
        )
        session.add(event)
        session.flush()
        session.add(
            PersonEventLinkRecord(
                person_id=person_id,
                event_id=event.id,
                role="target",
                confidence=0.9,
            )
        )
        session.commit()

    result = service.get_candidates(snapshot_id)

    assert result.total_count == 1
    assert result.candidates[0].event_count == 1
    assert result.candidates[0].last_event_date == event_date


def test_get_candidates_excludes_non_political_persons(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
) -> None:
    """Test that the query excludes non-political persons."""
    with session_factory() as session:
        # Create a non-political person
        person1_id = _create_person(
            session,
            "Non Political",
            "non political",
            "nonpolitical",
        )
        _create_persecution_classification(
            session,
            person1_id,
            status="non_political",
            confidence=0.9,
            reasons=["Not political"],
        )

        # Create a snapshot
        snapshot_id = _create_snapshot(session)

    result = service.get_candidates(snapshot_id)

    assert result.total_count == 0
    assert len(result.candidates) == 0


def _add_classification_version(
    session: Session,
    person_id: int,
    *,
    status: str,
    confidence: float,
    classifier_version: str,
    classified_at: datetime,
) -> None:
    session.add(
        PersecutionClassificationRecord(
            person_id=person_id,
            status=status,
            confidence=confidence,
            reasons=[],
            evidence_types=[],
            classifier_name="test-classifier",
            classifier_version=classifier_version,
            classified_at=classified_at,
        )
    )
    session.commit()


@pytest.mark.parametrize(
    ("old", "new", "expected_included"),
    [
        # A newer classifier version no longer considers the person political.
        (("political", 0.9), ("non_political", 0.95), False),
        (("political", 0.9), ("uncertain", 0.95), False),
        # A newer political classification below the threshold wins too.
        (("political", 0.9), ("political", 0.5), False),
        # A newer classifier version now considers the person political.
        (("non_political", 0.9), ("political", 0.8), True),
    ],
)
def test_get_candidates_uses_latest_classification_only(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
    old: tuple[str, float],
    new: tuple[str, float],
    expected_included: bool,
) -> None:
    with session_factory() as session:
        person_id = _create_person(
            session, "Иванов Иван Иванович", "иванов иван иванович", "ивановиваниванович"
        )
        _add_classification_version(
            session,
            person_id,
            status=old[0],
            confidence=old[1],
            classifier_version="1.0.0",
            classified_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        _add_classification_version(
            session,
            person_id,
            status=new[0],
            confidence=new[1],
            classifier_version="2.0.0",
            classified_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        snapshot_id = _create_snapshot(session)
        _create_match(session, person_id, snapshot_id, status="not_matched", confidence=0.8)

    result = service.get_candidates(snapshot_id)

    assert [c.person_id for c in result.candidates] == ([person_id] if expected_included else [])


def test_get_candidates_returns_person_once_with_several_political_classifications(
    session_factory: sessionmaker[Session],
    service: CandidateQueryService,
) -> None:
    with session_factory() as session:
        person_id = _create_person(
            session, "Иванов Иван Иванович", "иванов иван иванович", "ивановиваниванович"
        )
        for version, month, confidence in [("1.0.0", 1, 0.9), ("2.0.0", 6, 0.8)]:
            _add_classification_version(
                session,
                person_id,
                status="political",
                confidence=confidence,
                classifier_version=version,
                classified_at=datetime(2024, month, 1, tzinfo=UTC),
            )
        snapshot_id = _create_snapshot(session)
        _create_match(session, person_id, snapshot_id, status="not_matched", confidence=0.8)

    result = service.get_candidates(snapshot_id)

    assert [(c.person_id, c.persecution_confidence) for c in result.candidates] == [
        (person_id, 0.8)
    ]
    assert result.total_count == 1

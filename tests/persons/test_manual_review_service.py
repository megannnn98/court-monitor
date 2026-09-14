"""Tests for manual review service."""

import uuid

import pytest
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import PersonRecord
from persons.manual_review_service import (
    ReviewStatus,
    ReviewType,
    SqlAlchemyManualReviewService,
)


def _create_person(session: Session) -> int:
    """Helper to create a test person.

    These tests don't care about matching_key, only that the person row
    exists as a review subject.
    """
    person = PersonRecord(
        canonical_name="Test Person",
        normalized_name="test person",
        matching_key=f"testperson-{uuid.uuid4().hex}",
        status="active",
    )
    session.add(person)
    session.flush()
    return person.id


@pytest.fixture
def service() -> SqlAlchemyManualReviewService:
    """Create manual review service instance."""
    return SqlAlchemyManualReviewService()


def test_create_review(
    session_factory: sessionmaker[Session],
    service: SqlAlchemyManualReviewService,
) -> None:
    """Test creating a new review."""
    with session_factory() as session:
        person_id = _create_person(session)

        review_id = service.create_review(
            session,
            review_type=ReviewType.PERSON_MERGE,
            entity_id=person_id,
            reviewer_notes="Please review this merge",
        )

        assert review_id > 0

        review = service.get_review(session, review_id)
        assert review is not None
        assert review.subject_type == ReviewType.PERSON_MERGE
        assert review.subject_id == person_id
        assert review.decision == ReviewStatus.PENDING
        assert review.reviewer_note == "Please review this merge"
        assert review.created_at is not None
        assert review.reviewed_at is None


def test_update_review_status(
    session_factory: sessionmaker[Session],
    service: SqlAlchemyManualReviewService,
) -> None:
    """Test updating review status."""
    with session_factory() as session:
        person_id = _create_person(session)

        review_id = service.create_review(
            session,
            review_type=ReviewType.PERSON_MERGE,
            entity_id=person_id,
            reviewer_notes="Initial notes",
        )

        service.update_review_status(
            session,
            review_id=review_id,
            status=ReviewStatus.APPROVED,
            reviewer_notes="Approved after review",
        )

        review = service.get_review(session, review_id)
        assert review is not None
        assert review.decision == ReviewStatus.APPROVED
        assert review.reviewer_note == "Approved after review"
        assert review.reviewed_at is not None


def test_update_nonexistent_review_raises_error(
    session_factory: sessionmaker[Session],
    service: SqlAlchemyManualReviewService,
) -> None:
    """Test that updating nonexistent review raises error."""
    with session_factory() as session, pytest.raises(ValueError, match="Review 99999 not found"):
        service.update_review_status(
            session,
            review_id=99999,
            status=ReviewStatus.APPROVED,
            reviewer_notes="This should fail",
        )


def test_get_pending_reviews(
    session_factory: sessionmaker[Session],
    service: SqlAlchemyManualReviewService,
) -> None:
    """Test getting pending reviews."""
    with session_factory() as session:
        person1_id = _create_person(session)
        person2_id = _create_person(session)

        review1_id = service.create_review(
            session,
            review_type=ReviewType.PERSON_MERGE,
            entity_id=person1_id,
            reviewer_notes="Review 1",
        )

        review2_id = service.create_review(
            session,
            review_type=ReviewType.ROSFINMATCH,
            entity_id=person2_id,
            reviewer_notes="Review 2",
        )

        # Approve one review
        service.update_review_status(
            session,
            review_id=review1_id,
            status=ReviewStatus.APPROVED,
            reviewer_notes="Approved",
        )

        # Get all pending reviews
        pending = service.get_pending_reviews(session)
        assert len(pending) == 1
        assert pending[0].id == review2_id

        # Get pending reviews filtered by type
        pending_merge = service.get_pending_reviews(session, review_type=ReviewType.PERSON_MERGE)
        assert len(pending_merge) == 0

        pending_rf = service.get_pending_reviews(session, review_type=ReviewType.ROSFINMATCH)
        assert len(pending_rf) == 1
        assert pending_rf[0].id == review2_id


def test_get_pending_reviews_with_limit(
    session_factory: sessionmaker[Session],
    service: SqlAlchemyManualReviewService,
) -> None:
    """Test getting pending reviews with limit."""
    with session_factory() as session:
        # Create 5 reviews
        review_ids = []
        for i in range(5):
            person_id = _create_person(session)
            review_id = service.create_review(
                session,
                review_type=ReviewType.PERSON_MERGE,
                entity_id=person_id,
                reviewer_notes=f"Review {i}",
            )
            review_ids.append(review_id)

        # Get with limit
        pending = service.get_pending_reviews(session, limit=3)
        assert len(pending) == 3

        # Should be ordered by created_at desc (newest first)
        assert pending[0].id == review_ids[4]
        assert pending[1].id == review_ids[3]
        assert pending[2].id == review_ids[2]


def test_get_reviews_by_entity(
    session_factory: sessionmaker[Session],
    service: SqlAlchemyManualReviewService,
) -> None:
    """Test getting reviews for a specific entity."""
    with session_factory() as session:
        person1_id = _create_person(session)
        person2_id = _create_person(session)

        # Create multiple reviews for person1
        review1_id = service.create_review(
            session,
            review_type=ReviewType.PERSON_MERGE,
            entity_id=person1_id,
            reviewer_notes="Merge review 1",
        )

        service.create_review(
            session,
            review_type=ReviewType.ROSFINMATCH,
            entity_id=person1_id,
            reviewer_notes="RF match review",
        )

        # Create review for person2
        review3_id = service.create_review(
            session,
            review_type=ReviewType.PERSON_MERGE,
            entity_id=person2_id,
            reviewer_notes="Merge review 2",
        )

        # Get reviews for person1
        person1_reviews = service.get_reviews_by_entity(
            session,
            review_type=ReviewType.PERSON_MERGE,
            entity_id=person1_id,
        )
        assert len(person1_reviews) == 1
        assert person1_reviews[0].id == review1_id

        # Get all reviews for person1 (any type)
        # Note: get_reviews_by_entity requires review_type, so we need to check each type
        person1_merge = service.get_reviews_by_entity(
            session,
            review_type=ReviewType.PERSON_MERGE,
            entity_id=person1_id,
        )
        person1_rf = service.get_reviews_by_entity(
            session,
            review_type=ReviewType.ROSFINMATCH,
            entity_id=person1_id,
        )
        assert len(person1_merge) == 1
        assert len(person1_rf) == 1

        # Get reviews for person2
        person2_reviews = service.get_reviews_by_entity(
            session,
            review_type=ReviewType.PERSON_MERGE,
            entity_id=person2_id,
        )
        assert len(person2_reviews) == 1
        assert person2_reviews[0].id == review3_id


def test_review_status_constants() -> None:
    """Test that review status constants are correct."""
    assert ReviewStatus.PENDING == "pending"
    assert ReviewStatus.APPROVED == "approved"
    assert ReviewStatus.REJECTED == "rejected"
    assert ReviewStatus.NEEDS_MORE_INFO == "needs_more_info"


def test_review_type_constants() -> None:
    """Test that review type constants are correct."""
    assert ReviewType.PERSON_MERGE == "person_merge"
    assert ReviewType.ROSFINMATCH == "rosfinmatch"
    assert ReviewType.PERSECUTION_CLASSIFICATION == "persecution_classification"


def test_get_nonexistent_review_returns_none(
    session_factory: sessionmaker[Session],
    service: SqlAlchemyManualReviewService,
) -> None:
    """Test that getting nonexistent review returns None."""
    with session_factory() as session:
        review = service.get_review(session, 99999)
        assert review is None

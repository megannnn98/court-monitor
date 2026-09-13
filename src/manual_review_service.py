"""Manual review service for ambiguous cases."""

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from orm_models import ReviewRecordModel


class ReviewStatus:
    """Review status constants."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_MORE_INFO = "needs_more_info"


class ReviewType:
    """Review type constants."""

    PERSON_MERGE = "person_merge"
    ROSFINMATCH = "rosfinmatch"
    PERSECUTION_CLASSIFICATION = "persecution_classification"


class ManualReviewService(Protocol):
    """Protocol for manual review service."""

    def create_review(
        self,
        session: Session,
        review_type: str,
        entity_id: int,
        reviewer_notes: str,
    ) -> int:
        """Create a new review request."""
        ...

    def update_review_status(
        self,
        session: Session,
        review_id: int,
        status: str,
        reviewer_notes: str,
    ) -> None:
        """Update review status."""
        ...

    def get_pending_reviews(
        self,
        session: Session,
        review_type: str | None = None,
        limit: int = 100,
    ) -> list[ReviewRecordModel]:
        """Get pending reviews."""
        ...

    def get_review(self, session: Session, review_id: int) -> ReviewRecordModel | None:
        """Get a specific review."""
        ...


class SqlAlchemyManualReviewService:
    """SQLAlchemy implementation of manual review service."""

    def create_review(
        self,
        session: Session,
        review_type: str,
        entity_id: int,
        reviewer_notes: str,
    ) -> int:
        """Create a new review request.

        Args:
            session: Database session
            review_type: Type of review (person_merge, rosfinmatch, etc.)
            entity_id: ID of the entity being reviewed
            reviewer_notes: Initial notes from reviewer

        Returns:
            ID of the created review
        """
        review = ReviewRecordModel(
            subject_type=review_type,
            subject_id=entity_id,
            decision=ReviewStatus.PENDING,
            reviewer_note=reviewer_notes,
            created_at=datetime.now(UTC),
        )
        session.add(review)
        session.flush()
        return review.id

    def update_review_status(
        self,
        session: Session,
        review_id: int,
        status: str,
        reviewer_notes: str,
    ) -> None:
        """Update review status.

        Args:
            session: Database session
            review_id: ID of the review to update
            status: New status (approved, rejected, etc.)
            reviewer_notes: Updated notes from reviewer
        """
        review = session.get(ReviewRecordModel, review_id)
        if review is None:
            raise ValueError(f"Review {review_id} not found")

        review.decision = status
        review.reviewer_note = reviewer_notes
        review.reviewed_at = datetime.now(UTC)
        session.flush()

    def get_pending_reviews(
        self,
        session: Session,
        review_type: str | None = None,
        limit: int = 100,
    ) -> list[ReviewRecordModel]:
        """Get pending reviews.

        Args:
            session: Database session
            review_type: Optional filter by review type
            limit: Maximum number of reviews to return

        Returns:
            List of pending reviews
        """
        query = select(ReviewRecordModel).where(ReviewRecordModel.decision == ReviewStatus.PENDING)

        if review_type is not None:
            query = query.where(ReviewRecordModel.subject_type == review_type)

        query = query.order_by(ReviewRecordModel.created_at.desc()).limit(limit)

        return list(session.scalars(query).all())

    def get_review(self, session: Session, review_id: int) -> ReviewRecordModel | None:
        """Get a specific review.

        Args:
            session: Database session
            review_id: ID of the review

        Returns:
            Review record or None if not found
        """
        return session.get(ReviewRecordModel, review_id)

    def get_reviews_by_entity(
        self,
        session: Session,
        review_type: str,
        entity_id: int,
    ) -> list[ReviewRecordModel]:
        """Get all reviews for a specific entity.

        Args:
            session: Database session
            review_type: Type of review
            entity_id: ID of the entity

        Returns:
            List of reviews for the entity
        """
        query = (
            select(ReviewRecordModel)
            .where(
                ReviewRecordModel.subject_type == review_type,
                ReviewRecordModel.subject_id == entity_id,
            )
            .order_by(ReviewRecordModel.created_at.desc())
        )

        return list(session.scalars(query).all())

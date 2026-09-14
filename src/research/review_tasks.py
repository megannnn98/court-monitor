"""Explicit creation of persistent review tasks for research results.

A research report only marks `review_required`; nothing is persisted on read.
This service is the explicit action behind `POST /research/reviews`. It reuses
`review_records` via `ManualReviewService`, re-checks the review condition
against the current database with `ResearchReviewPolicy`, and stores only a
reason code and references — never free text.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import (
    PersecutionClassificationRecord,
    PersonRecord,
    ReviewRecordModel,
    RosfinMatchRecord,
)
from persecution.queries import latest_persecution_classification_ids
from persons.manual_review_service import ReviewStatus, ReviewType, SqlAlchemyManualReviewService
from persons.models import PersonStatus
from research.mapping import classification_from_record, rosfinmonitoring_from_match
from research.reports.models import ResearchReviewReason, ResearchReviewReasonDetail
from research.reports.review_policy import ResearchReviewPolicy

_CLASSIFICATION_REASONS = frozenset(
    {
        ResearchReviewReason.PERSECUTION_UNCERTAIN,
        ResearchReviewReason.PERSECUTION_NEEDS_REVIEW,
        ResearchReviewReason.LOW_CONFIDENCE,
    }
)
_ROSFIN_REASONS = frozenset(
    {
        ResearchReviewReason.ROSFIN_AMBIGUOUS,
        ResearchReviewReason.ROSFIN_NEEDS_REVIEW,
        ResearchReviewReason.ROSFIN_INSUFFICIENT_DATA,
    }
)


class ResearchReviewTaskRequest(BaseModel):
    """Which stored fact of which person a human should review, and why."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    person_id: int = Field(gt=0)
    reason: ResearchReviewReason
    # Required for persecution reasons (the reviewed classification).
    classification_id: int | None = Field(default=None, gt=0)
    # Required for Rosfinmonitoring reasons (the snapshot of the reviewed match).
    snapshot_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        if self.reason in _CLASSIFICATION_REASONS:
            if self.classification_id is None or self.snapshot_id is not None:
                raise ValueError(f"{self.reason.value} requires classification_id only")
        elif self.reason in _ROSFIN_REASONS:
            if self.snapshot_id is None or self.classification_id is not None:
                raise ValueError(f"{self.reason.value} requires snapshot_id only")
        else:
            # MISSING_EVIDENCE has no stored subject a review type exists for.
            raise ValueError(f"{self.reason.value} cannot be persisted as a review task")
        return self


class ResearchReviewTask(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    review_id: int
    # False when a pending review of the same subject already existed.
    created: bool
    subject_type: str
    subject_id: int
    person_id: int
    requested_reason: ResearchReviewReason
    # `review_records.reason` of the returned review. Differs from
    # `requested_reason` when an existing pending review of the same subject
    # was opened for another reason (e.g. the match was re-run in place).
    stored_reason: str | None
    decision: str = ReviewStatus.PENDING


class ResearchReviewSubjectNotFoundError(LookupError):
    pass


class ResearchReviewConditionNotMetError(ValueError):
    """The current data does not warrant this review (stale or wrong request)."""


class ResearchReviewTaskService:
    def __init__(
        self,
        *,
        review_service: SqlAlchemyManualReviewService | None = None,
        review_policy: ResearchReviewPolicy | None = None,
    ) -> None:
        self._review_service = review_service or SqlAlchemyManualReviewService()
        self._review_policy = review_policy or ResearchReviewPolicy()

    def create(self, session: Session, request: ResearchReviewTaskRequest) -> ResearchReviewTask:
        person = session.get(PersonRecord, request.person_id)
        if person is None or person.status != PersonStatus.ACTIVE.value:
            raise ResearchReviewSubjectNotFoundError(f"Active person {request.person_id} not found")

        if request.reason in _CLASSIFICATION_REASONS:
            subject_type, subject_id, confidence = self._classification_subject(session, request)
        else:
            subject_type, subject_id, confidence = self._rosfinmonitoring_subject(session, request)

        review_id, created = self._review_service.get_or_create_pending_review(
            session,
            review_type=subject_type,
            entity_id=subject_id,
            reason=request.reason.value,
            confidence=confidence,
        )
        stored = session.get(ReviewRecordModel, review_id)
        return ResearchReviewTask(
            review_id=review_id,
            created=created,
            subject_type=subject_type,
            subject_id=subject_id,
            person_id=request.person_id,
            requested_reason=request.reason,
            stored_reason=None if stored is None else stored.reason,
        )

    def _classification_subject(
        self, session: Session, request: ResearchReviewTaskRequest
    ) -> tuple[str, int, float]:
        record = session.get(PersecutionClassificationRecord, request.classification_id)
        if record is None or record.person_id != request.person_id:
            raise ResearchReviewSubjectNotFoundError(
                f"Classification {request.classification_id} of person {request.person_id} "
                "not found"
            )
        is_latest = session.scalar(
            select(PersecutionClassificationRecord.id).where(
                PersecutionClassificationRecord.id == record.id,
                PersecutionClassificationRecord.id.in_(latest_persecution_classification_ids()),
            )
        )
        if is_latest is None:
            raise ResearchReviewConditionNotMetError(
                f"Classification {record.id} is not the latest classification of the person"
            )
        self._require_reason(
            request, self._review_policy.persecution_reasons(classification_from_record(record))
        )
        return ReviewType.PERSECUTION_CLASSIFICATION, record.id, record.confidence

    def _rosfinmonitoring_subject(
        self, session: Session, request: ResearchReviewTaskRequest
    ) -> tuple[str, int, float]:
        assert request.snapshot_id is not None  # guaranteed by the request validator
        record = session.scalar(
            select(RosfinMatchRecord).where(
                RosfinMatchRecord.person_id == request.person_id,
                RosfinMatchRecord.snapshot_id == request.snapshot_id,
            )
        )
        if record is None:
            # NO_MATCH_RECORD: run the matcher; there is no match to review.
            raise ResearchReviewSubjectNotFoundError(
                f"No Rosfinmonitoring match of person {request.person_id} for snapshot "
                f"{request.snapshot_id}"
            )
        self._require_reason(
            request,
            self._review_policy.rosfinmonitoring_reasons(
                rosfinmonitoring_from_match(record, snapshot_id=request.snapshot_id)
            ),
        )
        return ReviewType.ROSFINMATCH, record.id, record.confidence

    @staticmethod
    def _require_reason(
        request: ResearchReviewTaskRequest, reasons: list[ResearchReviewReasonDetail]
    ) -> None:
        if request.reason not in {reason.code for reason in reasons}:
            current = ", ".join(reason.code.value for reason in reasons) or "none"
            raise ResearchReviewConditionNotMetError(
                f"Current data does not require review for {request.reason.value} "
                f"(current review reasons: {current})"
            )

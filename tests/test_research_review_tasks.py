"""Explicit, idempotent persistent review tasks for research results (PostgreSQL)."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from pydantic import ValidationError
from research_db_fixtures import FIXED_TIME, ResearchSeeder
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from manual_review_service import ReviewStatus, ReviewType, SqlAlchemyManualReviewService
from orm_models import ReviewRecordModel, RosfinMatchRecord
from research_reports.models import ResearchReviewReason
from research_review_tasks import (
    ResearchReviewConditionNotMetError,
    ResearchReviewSubjectNotFoundError,
    ResearchReviewTaskRequest,
    ResearchReviewTaskService,
)

SERVICE = ResearchReviewTaskService()


def _review_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(ReviewRecordModel)) or 0


def test_request_requires_the_reference_of_its_review_type() -> None:
    with pytest.raises(ValidationError, match="classification_id"):
        ResearchReviewTaskRequest(person_id=1, reason=ResearchReviewReason.PERSECUTION_UNCERTAIN)
    with pytest.raises(ValidationError, match="snapshot_id"):
        ResearchReviewTaskRequest(person_id=1, reason=ResearchReviewReason.ROSFIN_AMBIGUOUS)
    with pytest.raises(ValidationError, match="cannot be persisted"):
        ResearchReviewTaskRequest(
            person_id=1, reason=ResearchReviewReason.MISSING_EVIDENCE, classification_id=1
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        ResearchReviewTaskRequest.model_validate(
            {"person_id": 1, "reason": "rosfin_ambiguous", "snapshot_id": 1, "note": "LLM text"}
        )


def test_rosfinmonitoring_review_task_uses_the_match_record_and_is_idempotent(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        person_id = seed.person("Иван Иванов")
        snapshot_id = seed.snapshot()
        match_id = seed.match(person_id, snapshot_id, "ambiguous", 0.5)
        session.commit()

    task_request = ResearchReviewTaskRequest(
        person_id=person_id, reason=ResearchReviewReason.ROSFIN_AMBIGUOUS, snapshot_id=snapshot_id
    )
    with session_factory() as session:
        first = SERVICE.create(session, task_request)
        session.commit()
    with session_factory() as session:
        second = SERVICE.create(session, task_request)
        session.commit()

        assert first.created is True
        assert second.created is False
        assert second.review_id == first.review_id
        assert (first.subject_type, first.subject_id) == (ReviewType.ROSFINMATCH, match_id)
        assert _review_count(session) == 1
        record = session.get(ReviewRecordModel, first.review_id)
        assert record is not None
        assert record.decision == ReviewStatus.PENDING
        # A fixed reason code, never free text.
        assert record.reason == ResearchReviewReason.ROSFIN_AMBIGUOUS.value
        assert record.confidence == 0.5


def test_persecution_review_task_targets_the_latest_classification(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        person_id = seed.person("Иван Иванов")
        older = seed.classification(person_id, "uncertain", 0.4)
        latest = seed.classification(
            person_id,
            "uncertain",
            0.45,
            classifier_version="1.1.0",
            classified_at=FIXED_TIME + timedelta(days=1),
        )
        session.commit()

    with session_factory() as session:
        with pytest.raises(ResearchReviewConditionNotMetError, match="latest"):
            SERVICE.create(
                session,
                ResearchReviewTaskRequest(
                    person_id=person_id,
                    reason=ResearchReviewReason.PERSECUTION_UNCERTAIN,
                    classification_id=older,
                ),
            )
        task = SERVICE.create(
            session,
            ResearchReviewTaskRequest(
                person_id=person_id,
                reason=ResearchReviewReason.PERSECUTION_UNCERTAIN,
                classification_id=latest,
            ),
        )
        session.commit()

    assert (task.subject_type, task.subject_id, task.created) == (
        ReviewType.PERSECUTION_CLASSIFICATION,
        latest,
        True,
    )


def test_low_confidence_political_can_be_sent_to_review(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        person_id = seed.person("Иван Иванов")
        classification_id = seed.classification(person_id, "political", 0.5)
        session.commit()

        task = SERVICE.create(
            session,
            ResearchReviewTaskRequest(
                person_id=person_id,
                reason=ResearchReviewReason.LOW_CONFIDENCE,
                classification_id=classification_id,
            ),
        )

    assert task.created is True


@pytest.mark.parametrize(
    ("reason", "stored_status"),
    [
        (ResearchReviewReason.ROSFIN_AMBIGUOUS, "not_matched"),
        (ResearchReviewReason.ROSFIN_INSUFFICIENT_DATA, "ambiguous"),
    ],
)
def test_reason_that_does_not_match_stored_status_is_rejected(
    session_factory: sessionmaker[Session], reason: ResearchReviewReason, stored_status: str
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        person_id = seed.person("Иван Иванов")
        snapshot_id = seed.snapshot()
        seed.match(person_id, snapshot_id, stored_status, 0.8)
        session.commit()

        with pytest.raises(ResearchReviewConditionNotMetError):
            SERVICE.create(
                session,
                ResearchReviewTaskRequest(
                    person_id=person_id, reason=reason, snapshot_id=snapshot_id
                ),
            )
        assert _review_count(session) == 0


def test_no_match_record_has_nothing_to_review(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        person_id = seed.person("Иван Иванов")
        snapshot_id = seed.snapshot()
        session.commit()

        with pytest.raises(ResearchReviewSubjectNotFoundError):
            SERVICE.create(
                session,
                ResearchReviewTaskRequest(
                    person_id=person_id,
                    reason=ResearchReviewReason.ROSFIN_NEEDS_REVIEW,
                    snapshot_id=snapshot_id,
                ),
            )


def test_classification_of_another_person_is_not_found(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        person_id = seed.person("Иван Иванов")
        other_id = seed.person("Пётр Петров")
        classification_id = seed.classification(other_id, "uncertain", 0.4)
        session.commit()

        with pytest.raises(ResearchReviewSubjectNotFoundError):
            SERVICE.create(
                session,
                ResearchReviewTaskRequest(
                    person_id=person_id,
                    reason=ResearchReviewReason.PERSECUTION_UNCERTAIN,
                    classification_id=classification_id,
                ),
            )


def test_decided_review_allows_a_new_pending_task(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        person_id = seed.person("Иван Иванов")
        snapshot_id = seed.snapshot()
        seed.match(person_id, snapshot_id, "needs_review", 0.5)
        session.commit()
        task_request = ResearchReviewTaskRequest(
            person_id=person_id,
            reason=ResearchReviewReason.ROSFIN_NEEDS_REVIEW,
            snapshot_id=snapshot_id,
        )

        first = SERVICE.create(session, task_request)
        SqlAlchemyManualReviewService().update_review_status(
            session, first.review_id, ReviewStatus.REJECTED, "no"
        )
        second = SERVICE.create(session, task_request)

    assert second.created is True
    assert second.review_id != first.review_id


def test_pending_duplicate_insert_is_blocked_by_the_database(
    session_factory: sessionmaker[Session],
) -> None:
    review_service = SqlAlchemyManualReviewService()
    with session_factory() as session:
        first_id, first_created = review_service.get_or_create_pending_review(
            session, review_type=ReviewType.ROSFINMATCH, entity_id=5, reason="rosfin_ambiguous"
        )
        second_id, second_created = review_service.get_or_create_pending_review(
            session, review_type=ReviewType.ROSFINMATCH, entity_id=5, reason="rosfin_ambiguous"
        )

        assert (first_created, second_created) == (True, False)
        assert first_id == second_id
        with pytest.raises(IntegrityError):
            review_service.create_review(
                session, review_type=ReviewType.ROSFINMATCH, entity_id=5, reviewer_notes="dup"
            )


def test_existing_pending_review_reports_its_stored_reason(
    session_factory: sessionmaker[Session],
) -> None:
    # rosfin_matches rows are updated in place when matching is re-run, so the
    # subject id stays the same while the status (and review reason) changes.
    with session_factory() as session:
        seed = ResearchSeeder(session)
        person_id = seed.person("Иван Иванов")
        snapshot_id = seed.snapshot()
        match_id = seed.match(person_id, snapshot_id, "ambiguous", 0.5)
        session.commit()

        first = SERVICE.create(
            session,
            ResearchReviewTaskRequest(
                person_id=person_id,
                reason=ResearchReviewReason.ROSFIN_AMBIGUOUS,
                snapshot_id=snapshot_id,
            ),
        )
        match = session.get(RosfinMatchRecord, match_id)
        assert match is not None
        match.status = "insufficient_data"
        session.flush()
        second = SERVICE.create(
            session,
            ResearchReviewTaskRequest(
                person_id=person_id,
                reason=ResearchReviewReason.ROSFIN_INSUFFICIENT_DATA,
                snapshot_id=snapshot_id,
            ),
        )

    assert (second.created, second.review_id) == (False, first.review_id)
    assert second.requested_reason is ResearchReviewReason.ROSFIN_INSUFFICIENT_DATA
    assert second.stored_reason == ResearchReviewReason.ROSFIN_AMBIGUOUS.value


def test_concurrent_pending_review_waits_and_reuses_the_committed_row(
    session_factory: sessionmaker[Session],
) -> None:
    """Session B inserts the same subject while session A has not committed yet.

    PostgreSQL makes B's INSERT ... ON CONFLICT wait on A's uncommitted row;
    after A commits, B must return A's review instead of a duplicate or an error.
    """
    review_service = SqlAlchemyManualReviewService()
    outcome: dict[str, object] = {}

    def insert_in_second_session() -> None:
        try:
            with session_factory() as session:
                # Never hang the suite if the wait does not end.
                session.execute(text("SET LOCAL lock_timeout = '10s'"))
                outcome["result"] = review_service.get_or_create_pending_review(
                    session,
                    review_type=ReviewType.ROSFINMATCH,
                    entity_id=42,
                    reason="rosfin_ambiguous",
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001 - a thread must hand any error to the test
            outcome["error"] = exc

    with session_factory() as first:
        first_id, first_created = review_service.get_or_create_pending_review(
            first, review_type=ReviewType.ROSFINMATCH, entity_id=42, reason="rosfin_ambiguous"
        )
        second = threading.Thread(target=insert_in_second_session)
        second.start()
        second.join(timeout=1.0)
        # Still blocked on the uncommitted conflicting row.
        assert second.is_alive()
        first.commit()

    second.join(timeout=15.0)
    assert not second.is_alive()
    assert "error" not in outcome, outcome.get("error")
    assert first_created is True
    assert outcome["result"] == (first_id, False)
    with session_factory() as session:
        assert _review_count(session) == 1


def test_inactive_person_cannot_get_a_review_task(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        person_id = seed.person("Иван Иванов", status="merged")
        snapshot_id = seed.snapshot()
        seed.match(person_id, snapshot_id, "ambiguous", 0.5)
        session.commit()

        with pytest.raises(ResearchReviewSubjectNotFoundError, match="Active person"):
            SERVICE.create(
                session,
                ResearchReviewTaskRequest(
                    person_id=person_id,
                    reason=ResearchReviewReason.ROSFIN_AMBIGUOUS,
                    snapshot_id=snapshot_id,
                ),
            )

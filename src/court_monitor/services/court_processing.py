"""Court document processing state service (task §14)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from court_monitor.domain import models as _domain_models
from court_monitor.observability import get_logger
from court_monitor.storage.orm import CourtDocumentProcessing

_log = get_logger(__name__)


@dataclass
class CourtProcessingStats:
    """Counters for a single court's processing batch.

    Returned by ``process_all_pending_court_documents`` so callers (CLI, API)
    can display the breakdown instead of just ``processed=N``.
    """

    processed: int = 0
    review_created: int = 0
    no_match: int = 0
    temporary_failures: int = 0
    failed: int = 0

    def __int__(self) -> int:
        return self.processed


class ProcessingStatus:
    PENDING = "pending"
    REVIEW_CREATED = "review_created"
    PROCESSED_NO_MATCH = "processed_no_match"
    TEMPORARY_FAILURE = "temporary_failure"
    FAILED = "failed"

    TERMINAL = (REVIEW_CREATED, PROCESSED_NO_MATCH, FAILED)
    MAX_ATTEMPTS = 3


def get_or_create_processing_state(
    session: Session,
    *,
    document_id: int,
    court: str,
) -> CourtDocumentProcessing:
    """Return the processing row for ``(document_id, pipeline_version)``.

    Creates the row if it does not exist yet. Idempotent by that pair,
    matching the unique constraint in the DB schema.
    """
    pipeline_version = _domain_models.COURT_PIPELINE_VERSION
    row = session.execute(
        select(CourtDocumentProcessing).where(
            CourtDocumentProcessing.document_id == document_id,
            CourtDocumentProcessing.pipeline_version == pipeline_version,
        )
    ).scalar_one_or_none()
    if row is None:
        row = CourtDocumentProcessing(
            document_id=document_id,
            court=court,
            pipeline_version=pipeline_version,
            status=ProcessingStatus.PENDING,
        )
        session.add(row)
        session.flush()
    return row


def mark_processing_success(
    session: Session,
    state: CourtDocumentProcessing,
    *,
    result_count: int,
    candidate_count: int,
) -> None:
    """Update state after a successful processing pass.

    ``candidate_count > 0`` → ``review_created`` (operator must decide).
    ``candidate_count == 0`` → ``processed_no_match`` (no retry by default).
    """
    state.status = (
        ProcessingStatus.REVIEW_CREATED
        if candidate_count > 0
        else ProcessingStatus.PROCESSED_NO_MATCH
    )
    state.last_attempt_at = datetime.now(UTC)
    state.attempt_count += 1
    state.result_count = result_count
    state.candidate_count = candidate_count
    state.last_error = None
    session.flush()


def mark_processing_temporary_failure(
    session: Session,
    state: CourtDocumentProcessing,
    *,
    error: str,
) -> None:
    """Retryable failure. Caller should retry up to ``MAX_ATTEMPTS``.

    When ``attempt_count`` reaches ``MAX_ATTEMPTS`` the state auto-transitions
    to ``failed`` — no more automatic retries after that.
    """
    state.last_attempt_at = datetime.now(UTC)
    state.attempt_count += 1
    state.last_error = error

    if state.attempt_count >= ProcessingStatus.MAX_ATTEMPTS:
        state.status = ProcessingStatus.FAILED
    else:
        state.status = ProcessingStatus.TEMPORARY_FAILURE
    session.flush()


def mark_processing_failed(
    session: Session,
    state: CourtDocumentProcessing,
    *,
    error: str,
) -> None:
    """Permanent failure — needs manual intervention."""
    state.status = ProcessingStatus.FAILED
    state.last_attempt_at = datetime.now(UTC)
    state.attempt_count += 1
    state.last_error = error
    session.flush()


def should_process(state: CourtDocumentProcessing) -> bool:
    """True if the document should be re-processed right now.

    ``pending`` and ``temporary_failure`` (with headroom on attempts) re-run;
    everything else (``review_created``, ``processed_no_match``, ``failed``)
    waits for an explicit ``--reprocess``.
    """
    if state.status == ProcessingStatus.PENDING:
        return True
    if state.status == ProcessingStatus.TEMPORARY_FAILURE:
        return state.attempt_count < ProcessingStatus.MAX_ATTEMPTS
    return False

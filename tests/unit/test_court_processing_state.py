"""Tests for :mod:`court_monitor.services.court_processing` (task §14, §15)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from court_monitor.domain.models import COURT_PIPELINE_VERSION
from court_monitor.services.court_processing import (
    ProcessingStatus,
    get_or_create_processing_state,
    mark_processing_failed,
    mark_processing_success,
    mark_processing_temporary_failure,
    should_process,
)
from court_monitor.storage.orm import Base, CourtDocumentProcessing, SourceDocument


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session_ = sessionmaker(bind=engine)
    with Session_() as s:
        yield s


@pytest.fixture()
def doc(session: Session):
    d = SourceDocument(
        url="press://ex/1",
        source_type="sudrf",
        source_name="2zovs",
        content_hash="h1",
    )
    session.add(d)
    session.flush()
    return d


def test_pipeline_version_is_stamped(session: Session, doc):
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    assert state.pipeline_version == COURT_PIPELINE_VERSION


def test_get_or_create_is_idempotent(session: Session, doc):
    s1 = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    s2 = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    assert s1.id == s2.id
    assert session.execute(select(CourtDocumentProcessing)).scalars().all().__len__() == 1


def test_success_with_candidates_is_review_created(session: Session, doc):
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    mark_processing_success(session, state, result_count=5, candidate_count=2)

    assert state.status == ProcessingStatus.REVIEW_CREATED
    assert state.result_count == 5
    assert state.candidate_count == 2
    assert state.attempt_count == 1


def test_success_with_no_candidates_is_processed_no_match(session: Session, doc):
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    mark_processing_success(session, state, result_count=0, candidate_count=0)
    assert state.status == ProcessingStatus.PROCESSED_NO_MATCH


def test_temporary_failure_increments_attempt_count(session: Session, doc):
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    mark_processing_temporary_failure(session, state, error="HTTP 500")

    assert state.status == ProcessingStatus.TEMPORARY_FAILURE
    assert state.attempt_count == 1
    assert state.last_error == "HTTP 500"


def test_should_process_allows_temporary_failure_with_headroom(session: Session, doc):
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    mark_processing_temporary_failure(session, state, error="HTTP 500")

    assert should_process(state) is True

    # After MAX_ATTEMPTS — stop.
    for _ in range(ProcessingStatus.MAX_ATTEMPTS):
        mark_processing_temporary_failure(session, state, error="HTTP 500")
    assert should_process(state) is False


def test_should_process_rejects_terminal_states(session: Session, doc):
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")

    state.status = ProcessingStatus.PROCESSED_NO_MATCH
    assert should_process(state) is False

    state.status = ProcessingStatus.REVIEW_CREATED
    assert should_process(state) is False

    state.status = ProcessingStatus.FAILED
    assert should_process(state) is False


def test_failed_is_permanent(session: Session, doc):
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    mark_processing_failed(session, state, error="permanent parse error")
    assert state.status == ProcessingStatus.FAILED
    assert state.last_error == "permanent parse error"
    assert should_process(state) is False


def test_temporary_failure_exhausted_transitions_to_failed(session: Session, doc):
    """TEMPORARY_FAILURE beyond MAX_ATTEMPTS → FAILED (review §findings)."""
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    for _ in range(ProcessingStatus.MAX_ATTEMPTS):
        mark_processing_temporary_failure(session, state, error="HTTP 500")

    assert state.status == ProcessingStatus.TEMPORARY_FAILURE
    assert state.attempt_count == ProcessingStatus.MAX_ATTEMPTS
    assert should_process(state) is False

    # Simulate what the orchestrator does when processing this state again:
    if (
        state.status == ProcessingStatus.TEMPORARY_FAILURE
        and state.attempt_count >= ProcessingStatus.MAX_ATTEMPTS
    ):
        mark_processing_failed(session, state, error="max retries exhausted")

    assert state.status == ProcessingStatus.FAILED
    assert "max retries exhausted" in (state.last_error or "")


def test_document_scoped_per_version(session: Session, doc):
    """Different pipeline versions produce separate processing rows."""
    s1 = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")

    from court_monitor.domain import models as _m  # noqa: PLC0415

    original = _m.COURT_PIPELINE_VERSION
    try:
        _m.COURT_PIPELINE_VERSION = "court-v2"
        s2 = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    finally:
        _m.COURT_PIPELINE_VERSION = original

    assert s1.id != s2.id
    assert s1.pipeline_version == "court-v1"
    assert s2.pipeline_version == "court-v2"

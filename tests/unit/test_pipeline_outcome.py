"""Tests for pipeline outcome tracking and state transitions (correctness pass)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from court_monitor.services.court_orchestrator import (
    PipelineOutcome,
    _apply_outcome_to_state,
)
from court_monitor.services.court_processing import (
    ProcessingStatus,
    get_or_create_processing_state,
)
from court_monitor.sources.sudrf_case_search import (
    SudrfPermanentError,
    SudrfTemporaryError,
)
from court_monitor.storage.orm import Base, SourceDocument


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
        parser_version="sudrf-press-0.2",
        parser_status="parsed",
    )
    session.add(d)
    session.flush()
    return d


def test_all_cards_failed_temporary(session: Session, doc):
    """All case cards failed with temporary error → temporary_failure."""
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    outcome = PipelineOutcome(
        search_succeeded=True,
        search_result_count=3,
        cards_evaluated=0,
        cards_transport_failed=3,
        last_transport_error=SudrfTemporaryError("timeout"),
    )
    _apply_outcome_to_state(session, state, outcome, doc.id)
    assert state.status == ProcessingStatus.TEMPORARY_FAILURE


def test_all_cards_failed_permanent(session: Session, doc):
    """All case cards failed with permanent error → failed."""
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    outcome = PipelineOutcome(
        search_succeeded=True,
        search_result_count=3,
        cards_evaluated=0,
        cards_transport_failed=3,
        last_transport_error=SudrfPermanentError("HTTP 404"),
    )
    _apply_outcome_to_state(session, state, outcome, doc.id)
    assert state.status == ProcessingStatus.FAILED


def test_partial_failure_no_candidate(session: Session, doc):
    """Partial card failure, no candidate → temporary_failure (not no_match)."""
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    outcome = PipelineOutcome(
        search_succeeded=True,
        search_result_count=3,
        cards_evaluated=2,
        cards_transport_failed=1,
    )
    _apply_outcome_to_state(session, state, outcome, doc.id)
    assert state.status == ProcessingStatus.TEMPORARY_FAILURE
    assert state.status != ProcessingStatus.PROCESSED_NO_MATCH


def test_all_evaluated_no_candidate(session: Session, doc):
    """All cards evaluated, zero candidates → processed_no_match."""
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    outcome = PipelineOutcome(
        search_succeeded=True,
        search_result_count=3,
        cards_evaluated=3,
        cards_transport_failed=0,
    )
    _apply_outcome_to_state(session, state, outcome, doc.id)
    assert state.status == ProcessingStatus.PROCESSED_NO_MATCH


def test_zero_search_results(session: Session, doc):
    """Search returned 0 results → processed_no_match."""
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    outcome = PipelineOutcome(
        search_succeeded=True,
        search_result_count=0,
    )
    _apply_outcome_to_state(session, state, outcome, doc.id)
    assert state.status == ProcessingStatus.PROCESSED_NO_MATCH


def test_no_search_criteria(session: Session, doc):
    """No search criteria (article+person both None) → failed, NOT no_match."""
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    outcome = PipelineOutcome(
        search_succeeded=False,
        search_result_count=0,
    )
    _apply_outcome_to_state(session, state, outcome, doc.id)
    assert state.status == ProcessingStatus.FAILED
    assert state.status != ProcessingStatus.PROCESSED_NO_MATCH


def test_all_cards_parse_failed(session: Session, doc):
    """All cards failed with parse errors → temporary_failure, NOT no_match."""
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    outcome = PipelineOutcome(
        search_succeeded=True,
        search_result_count=3,
        cards_evaluated=0,
        cards_transport_failed=0,
        cards_parse_failed=3,
    )
    _apply_outcome_to_state(session, state, outcome, doc.id)
    assert state.status == ProcessingStatus.TEMPORARY_FAILURE
    assert state.status != ProcessingStatus.PROCESSED_NO_MATCH


def test_partial_parse_failure_no_candidate(session: Session, doc):
    """Partial parse failure, no candidate → temporary_failure."""
    state = get_or_create_processing_state(session, document_id=doc.id, court="2zovs")
    outcome = PipelineOutcome(
        search_succeeded=True,
        search_result_count=3,
        cards_evaluated=2,
        cards_transport_failed=0,
        cards_parse_failed=1,
    )
    _apply_outcome_to_state(session, state, outcome, doc.id)
    assert state.status == ProcessingStatus.TEMPORARY_FAILURE

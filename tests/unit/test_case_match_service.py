"""Tests for case match candidate service."""

from __future__ import annotations

from court_monitor.matching.case_matching import CaseMatchResult, MatchMissing, MatchSignal
from court_monitor.parsers.sud_delo import ParsedCaseCard
from court_monitor.services.case_match_service import (
    create_case_match_candidate,
    list_pending_case_match_candidates,
)
from court_monitor.storage.orm import Case, SourceDocument


def _make_doc(db_session) -> SourceDocument:
    doc = SourceDocument(
        url="https://court.local/press/1",
        source_type="sudrf",
        source_name="2zovs",
        content_hash="test-hash-001",
    )
    db_session.add(doc)
    db_session.flush()
    return doc


def _make_case(db_session) -> Case:
    case = Case(
        court="2-й Западный окружной военный суд",
        case_number="1-100/2026",
        case_uid="test-uid-001",
        status="active",
    )
    db_session.add(case)
    db_session.flush()
    return case


def _make_match_result(confidence: float = 0.65) -> CaseMatchResult:
    return CaseMatchResult(
        case_card=ParsedCaseCard(case_number="1-100/2026"),
        confidence=confidence,
        signals=[
            MatchSignal(
                signal_type="article",
                description="Совпадает статья 205.1",
                criteria_value="205.1",
                case_value="ст.205.1 ч.1 УК РФ",
                weight=0.35,
            ),
            MatchSignal(
                signal_type="date",
                description="Совпадает дата 2026-04-02",
                criteria_value="2026-04-02",
                case_value="2026-04-02",
                weight=0.30,
            ),
        ],
        missing=[
            MatchMissing(field_name="court", description="Суд не совпадает"),
        ],
    )


def test_create_case_match_candidate_creates(db_session):
    """Test that a new match candidate is created."""
    doc = _make_doc(db_session)
    case = _make_case(db_session)
    result = _make_match_result()
    db_session.commit()

    candidate, created = create_case_match_candidate(db_session, doc, case, result)
    db_session.commit()

    assert created is True
    assert candidate.id is not None
    assert candidate.source_document_id == doc.id
    assert candidate.case_id == case.id
    assert candidate.score == 0.65
    assert candidate.status == "pending"
    assert candidate.signals_json is not None
    assert '"signal_type": "article"' in candidate.signals_json


def test_create_case_match_candidate_idempotent(db_session):
    """Test that re-creating same candidate updates instead of duplicating."""
    doc = _make_doc(db_session)
    case = _make_case(db_session)
    result1 = _make_match_result(confidence=0.65)
    db_session.commit()

    candidate1, created1 = create_case_match_candidate(db_session, doc, case, result1)
    db_session.commit()
    assert created1 is True

    # Second call with same doc+case — should update
    result2 = _make_match_result(confidence=0.85)
    candidate2, created2 = create_case_match_candidate(db_session, doc, case, result2)
    db_session.commit()

    assert created2 is False
    assert candidate2.id == candidate1.id
    assert candidate2.score == 0.85  # Updated score
    assert candidate1.score == 0.85  # Also updated (same object after commit)


def test_list_pending_case_match_candidates(db_session):
    """Test listing pending candidates ordered by score."""
    doc = _make_doc(db_session)

    # Create two cases with different scores
    for i, score in enumerate([0.65, 0.85, 0.30]):
        case = Case(
            court="2-й Западный окружной военный суд",
            case_number=f"1-{100 + i}/2026",
            case_uid=f"test-uid-{i:03d}",
            status="active",
        )
        db_session.add(case)
        db_session.flush()

        result = _make_match_result(confidence=score)
        create_case_match_candidate(db_session, doc, case, result)
    db_session.commit()

    pending = list_pending_case_match_candidates(db_session, limit=10)

    assert len(pending) == 3
    # Should be sorted by score descending
    assert pending[0].score >= pending[1].score >= pending[2].score
    assert pending[0].score == 0.85

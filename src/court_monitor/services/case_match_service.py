"""Service for creating and managing case match candidates."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from court_monitor.matching.case_matching import CaseMatchResult
from court_monitor.observability import get_logger
from court_monitor.storage.orm import Case, CaseMatchCandidate, SourceDocument

_log = get_logger(__name__)


def create_case_match_candidate(
    session: Session,
    source_document: SourceDocument,
    case: Case,
    match_result: CaseMatchResult,
) -> tuple[CaseMatchCandidate, bool]:
    """Create a case match candidate from a match result.

    Idempotent: if candidate already exists (same source_document + case),
    updates it instead of creating a duplicate.

    Args:
        session: Database session
        source_document: Press release document
        case: Matched court case
        match_result: Result from match_press_release_to_case()

    Returns:
        Tuple of (CaseMatchCandidate, created) where created indicates if new
    """
    # Try to find existing candidate
    existing = session.execute(
        select(CaseMatchCandidate).where(
            CaseMatchCandidate.source_document_id == source_document.id,
            CaseMatchCandidate.case_id == case.id,
        )
    ).scalar_one_or_none()

    created = existing is None

    # Serialize match data to JSON
    signals_json = json.dumps(
        [
            {
                "signal_type": s.signal_type,
                "description": s.description,
                "criteria_value": s.criteria_value,
                "case_value": s.case_value,
                "weight": s.weight,
            }
            for s in match_result.signals
        ]
    )

    missing_json = json.dumps(
        [
            {
                "field_name": m.field_name,
                "description": m.description,
            }
            for m in match_result.missing
        ]
    )

    conflicts_json = json.dumps(match_result.conflicts)

    if created:
        # Create new candidate
        candidate = CaseMatchCandidate(
            source_document_id=source_document.id,
            case_id=case.id,
            score=match_result.confidence,
            signals_json=signals_json,
            missing_json=missing_json,
            conflicts_json=conflicts_json,
            status="pending",
        )
        session.add(candidate)

        _log.info(
            "case_match_candidate.created",
            candidate_id=candidate.id,
            source_document_id=source_document.id,
            case_id=case.id,
            score=candidate.score,
        )
    else:
        # Update existing candidate
        existing.score = match_result.confidence
        existing.signals_json = signals_json
        existing.missing_json = missing_json
        existing.conflicts_json = conflicts_json

        _log.info(
            "case_match_candidate.updated",
            candidate_id=existing.id,
            source_document_id=source_document.id,
            case_id=case.id,
            score=existing.score,
        )

        candidate = existing

    session.commit()

    return candidate, created


def list_pending_case_match_candidates(
    session: Session,
    limit: int = 100,
) -> list[CaseMatchCandidate]:
    """List pending case match candidates for operator review.

    Args:
        session: Database session
        limit: Maximum number of candidates to return

    Returns:
        List of pending candidates, ordered by score (descending)
    """
    return list(
        session.execute(
            select(CaseMatchCandidate)
            .where(CaseMatchCandidate.status == "pending")
            .order_by(CaseMatchCandidate.score.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )

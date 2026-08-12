"""Service for creating and managing case match candidates."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from court_monitor.matching.case_matching import CaseMatchResult
from court_monitor.observability import get_logger
from court_monitor.storage.orm import (
    AuditLog,
    Case,
    CaseMatchCandidate,
    ReviewItem,
    SourceDocument,
)

_log = get_logger(__name__)


class CaseMatchDecision:
    """Operator decision on a case match candidate (task §11)."""

    CONFIRM = "confirm"
    REJECT = "reject"
    INSUFFICIENT = "insufficient"

    ALL = (CONFIRM, REJECT, INSUFFICIENT)


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
        assert existing is not None
        existing.score = match_result.confidence
        existing.signals_json = signals_json
        existing.missing_json = missing_json
        existing.conflicts_json = conflicts_json
        candidate = existing

        _log.info(
            "case_match_candidate.updated",
            candidate_id=candidate.id,
            source_document_id=source_document.id,
            case_id=case.id,
            score=candidate.score,
        )

    session.flush()

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


def review_case_match_candidate(
    session: Session,
    *,
    candidate_id: int,
    decision: str,
    actor: str,
    comment: str | None = None,
) -> CaseMatchCandidate:
    """Apply an operator decision to a case match candidate (task §11).

    Args:
        session: Active ORM session (caller owns the transaction).
        candidate_id: ID of the CaseMatchCandidate under review.
        decision: One of :attr:`CaseMatchDecision.ALL`.
        actor: Free-form identifier of the human who made the decision
            (typically the email / username passed in from the CLI or web).
        comment: Optional reason for the decision.

    Returns:
        The updated :class:`CaseMatchCandidate` (flushed, not committed).

    Raises:
        ValueError: ``decision`` is not recognised.
        LookupError: no candidate exists with ``candidate_id``.

    Atomic effects on decision:
        * ``confirm``     — candidate.status = confirmed, linked ReviewItem resolved.
        * ``reject``      — candidate.status = rejected, linked ReviewItem resolved.
        * ``insufficient`` — candidate.status stays pending, linked ReviewItem
          resolved with a note; the candidate surfaces again the next time
          ``list_pending_case_match_candidates`` is called.

    No decision auto-confirms any status from the pipeline side; only the
    operator can move a candidate out of ``pending``. Even a confidence of
    ``1.0`` lands as ``status="pending"`` in the audit log (task §13).
    """
    if decision not in CaseMatchDecision.ALL:
        raise ValueError(f"decision must be one of {CaseMatchDecision.ALL}, got {decision!r}")

    candidate = session.get(CaseMatchCandidate, candidate_id)
    if candidate is None:
        raise LookupError(f"CaseMatchCandidate id={candidate_id} not found")

    old_status = candidate.status
    now = datetime.now(UTC)

    if decision == CaseMatchDecision.CONFIRM:
        candidate.status = "confirmed"
    elif decision == CaseMatchDecision.REJECT:
        candidate.status = "rejected"
    # INSUFFICIENT intentionally leaves ``status`` as "pending".

    candidate.reviewed_by = actor
    candidate.reviewed_at = now
    candidate.review_comment = comment

    # Resolve the linked ReviewItem (if any). We locate it via
    # ``case_match_candidate_id`` — the canonical link (task §10) — falling
    # back to the legacy ``source_id``-based lookup for rows that predate it.
    review_item = session.execute(
        select(ReviewItem).where(
            ReviewItem.case_match_candidate_id == candidate.id,
            ReviewItem.status == "pending",
        )
    ).scalar_one_or_none()
    if review_item is None:
        review_item = session.execute(
            select(ReviewItem).where(
                ReviewItem.item_type == "court_case_match",
                ReviewItem.source_id == str(candidate.case_id),
                ReviewItem.status == "pending",
            )
        ).scalar_one_or_none()

    if review_item is not None:
        review_item.status = "resolved"
        review_item.resolved_at = now
        review_item.resolved_by = actor
        review_item.resolution_comment = comment or decision
        session.flush()

    # Audit trail — append-only, no updates to this row ever.
    session.add(
        AuditLog(
            actor=actor,
            action=f"case_match.{decision}",
            object_type="case_match_candidate",
            object_id=candidate.id,
            old_value_json=json.dumps({"status": old_status}, ensure_ascii=False),
            new_value_json=json.dumps(
                {"status": candidate.status, "comment": comment}, ensure_ascii=False
            ),
        )
    )

    _log.info(
        "case_match_candidate.reviewed",
        candidate_id=candidate.id,
        decision=decision,
        actor=actor,
        old_status=old_status,
        new_status=candidate.status,
    )

    session.flush()
    return candidate


def get_case_match_candidate(session: Session, candidate_id: int) -> CaseMatchCandidate | None:
    """Load a single candidate by id, or return None if absent."""
    return session.get(CaseMatchCandidate, candidate_id)

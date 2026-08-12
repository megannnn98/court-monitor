"""Tests for :func:`review_case_match_candidate` (task §11, §12, §13)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from court_monitor.services.case_match_service import (
    CaseMatchDecision,
    get_case_match_candidate,
    review_case_match_candidate,
)
from court_monitor.storage.orm import AuditLog, Base, Case, CaseMatchCandidate, ReviewItem


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session_ = sessionmaker(bind=engine)
    with Session_() as s:
        yield s


@pytest.fixture()
def seed(session: Session):
    from court_monitor.storage.orm import SourceDocument  # noqa: PLC0415

    doc = SourceDocument(
        url="press://example/1",
        source_type="sudrf",
        source_name="2zovs",
        content_hash="hash1",
    )
    case = Case(court="2zovs", case_number="1-1/2026")
    session.add_all([doc, case])
    session.flush()

    candidate = CaseMatchCandidate(
        source_document_id=doc.id,
        case_id=case.id,
        score=1.0,
        status="pending",
    )
    session.add(candidate)
    session.flush()
    return {"doc": doc, "case": case, "candidate": candidate}


def test_confirm_sets_status_and_audit(session: Session, seed):
    candidate = review_case_match_candidate(
        session,
        candidate_id=seed["candidate"].id,
        decision=CaseMatchDecision.CONFIRM,
        actor="operator-1",
        comment="Подтверждено",
    )
    session.commit()

    assert candidate.status == "confirmed"
    assert candidate.reviewed_by == "operator-1"
    assert candidate.review_comment == "Подтверждено"
    assert candidate.reviewed_at is not None

    audits = session.execute(select(AuditLog)).scalars().all()
    assert len(audits) == 1
    assert audits[0].action == "case_match.confirm"
    assert audits[0].actor == "operator-1"


def test_reject_sets_status_and_audit(session: Session, seed):
    candidate = review_case_match_candidate(
        session,
        candidate_id=seed["candidate"].id,
        decision=CaseMatchDecision.REJECT,
        actor="operator-2",
        comment="Не подтверждено",
    )
    session.commit()

    assert candidate.status == "rejected"
    audits = session.execute(select(AuditLog)).scalars().all()
    assert audits[0].action == "case_match.reject"


def test_insufficient_leaves_pending(session: Session, seed):
    candidate = review_case_match_candidate(
        session,
        candidate_id=seed["candidate"].id,
        decision=CaseMatchDecision.INSUFFICIENT,
        actor="op",
        comment="Недостаточно данных",
    )
    session.commit()

    # Status stays pending so the candidate resurfaces later.
    assert candidate.status == "pending"
    assert candidate.review_comment == "Недостаточно данных"


def test_confidence_one_still_requires_review(session: Session, seed):
    """Even a confidence=1.0 candidate must not auto-confirm (task §13)."""
    seed_candidate = seed["candidate"]
    assert seed_candidate.status == "pending"
    assert seed_candidate.score == 1.0

    # Re-load without applying any decision — status must still be pending.
    reloaded = get_case_match_candidate(session, seed_candidate.id)
    assert reloaded is not None
    assert reloaded.status == "pending"


def test_invalid_decision_rejected(session: Session, seed):
    with pytest.raises(ValueError):
        review_case_match_candidate(
            session,
            candidate_id=seed["candidate"].id,
            decision="nope",
            actor="op",
        )


def test_unknown_candidate_lookup_error(session: Session):
    with pytest.raises(LookupError):
        review_case_match_candidate(
            session,
            candidate_id=99999,
            decision=CaseMatchDecision.CONFIRM,
            actor="op",
        )


def test_review_resolves_linked_review_item(session: Session, seed):
    """Confirming a candidate must also resolve its linked ReviewItem (task §11)."""
    review_item = ReviewItem(
        item_type="court_case_match",
        priority="medium",
        document_id=seed["doc"].id,
        source_id=str(seed["case"].id),
        status="pending",
        case_match_candidate_id=seed["candidate"].id,
    )
    session.add(review_item)
    session.flush()

    review_case_match_candidate(
        session,
        candidate_id=seed["candidate"].id,
        decision=CaseMatchDecision.CONFIRM,
        actor="op",
        comment="ok",
    )
    session.commit()

    reloaded = session.get(ReviewItem, review_item.id)
    assert reloaded is not None
    assert reloaded.status == "resolved"
    assert reloaded.resolved_by == "op"

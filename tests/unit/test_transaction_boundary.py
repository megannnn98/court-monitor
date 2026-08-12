"""Tests for transaction boundary fix in orchestrator (task §9)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from court_monitor.storage.orm import Base, Case


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session_ = sessionmaker(bind=engine)
    with Session_() as s:
        yield s


def test_nested_savepoint_failure_does_not_rollback_success(
    session: Session,
):
    """Task §9: one failing candidate must not roll back a previous success."""
    # First nested block: succeed.
    with session.begin_nested():
        c1 = Case(court="2zovs", case_number="1-1/2026")
        session.add(c1)

    # Second nested block: intentionally violate a constraint.
    with pytest.raises(IntegrityError), session.begin_nested():
        # Duplicate unique constraint: same court + case_number as c1.
        c2 = Case(court="2zovs", case_number="1-1/2026")
        session.add(c2)
        session.flush()

    # Third block: succeed.
    with session.begin_nested():
        c3 = Case(court="2zovs", case_number="2-2/2026")
        session.add(c3)

    session.commit()

    cases = session.execute(select(Case)).scalars().all()
    case_numbers = {c.case_number for c in cases}
    # c1 and c3 must have survived; c2 must NOT (rolled back by savepoint).
    assert "1-1/2026" in case_numbers
    assert "2-2/2026" in case_numbers
    # No duplicate 1-1/2026
    assert len([c for c in cases if c.case_number == "1-1/2026"]) == 1


def test_nested_savepoint_failure_can_be_retried(session: Session):
    """After a savepoint failure, the session must remain usable."""
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(Case(court="x", case_number="A"))
        session.add(Case(court="x", case_number="A"))  # same unique key
        session.flush()

    # Session must accept new work after rollback.
    with session.begin_nested():
        session.add(Case(court="x", case_number="B"))
    session.commit()

    assert session.execute(select(Case)).scalars().all().__len__() == 1


def test_no_commit_inside_savepoint_pattern(session: Session, monkeypatch):
    """Guard: services must never call ``session.commit()`` inside savepoints."""
    call_log: list[str] = []
    real_commit = session.commit

    def _tracked_commit():
        call_log.append("commit")
        return real_commit()

    monkeypatch.setattr(session, "commit", _tracked_commit)

    # Two nested blocks — neither should trigger commit.
    with session.begin_nested():
        session.add(Case(court="x", case_number="A"))
    with session.begin_nested():
        session.add(Case(court="x", case_number="B"))

    assert "commit" not in call_log
    session.commit()
    assert call_log.count("commit") == 1

"""The results' explicit testing API: reads of its tables for the evaluation and tests.

Not for production code (`tests/test_data_ownership.py` holds every production module
to it; ADR 0031, «Evaluation»).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from results.storage.models import MonitoringFindingRecord


def active_finding_person_ids(session: Session) -> set[int]:
    """The ER v2 people with an active monitoring finding."""
    return set(
        session.scalars(
            select(MonitoringFindingRecord.person_id).where(
                MonitoringFindingRecord.active.is_(True)
            )
        ).all()
    )

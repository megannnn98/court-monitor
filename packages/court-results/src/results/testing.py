"""The results' explicit testing API: reads of its tables for the evaluation and tests.

Not for production code (`tests/test_data_ownership.py` holds every production module
to it; ADR 0031, «Evaluation»).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from results.storage.models import MonitoringFindingRecord

# The tables a disposable test or evaluation database is wiped of between runs
# (`db.maintenance.truncate_disposable_tables`); a cache of model answers worth keeping
# is not among them.
DISPOSABLE_TABLES = (
    "chat_questions",
    "entity_done_marks",
    "monitoring_findings",
)


def active_finding_person_ids(session: Session) -> set[int]:
    """The ER v2 people with an active monitoring finding."""
    return set(
        session.scalars(
            select(MonitoringFindingRecord.person_id).where(
                MonitoringFindingRecord.active.is_(True)
            )
        ).all()
    )


def finding_first_runs(session: Session) -> list[tuple[int, int | None]]:
    """(ER v2 person, the monitoring run that first found it) of every finding."""
    return [
        (person_id, run_id)
        for person_id, run_id in session.execute(
            select(MonitoringFindingRecord.person_id, MonitoringFindingRecord.first_seen_run_id)
        )
    ]

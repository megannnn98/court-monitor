"""The cases' explicit testing API: what a disposable test or evaluation database is wiped of.

Not for production code (`tests/test_data_ownership.py` holds every production module to it;
ADR 0031, «Evaluation»).
"""

from __future__ import annotations

# The tables a disposable database is emptied of between runs
# (`db.maintenance.truncate_disposable_tables`).
DISPOSABLE_TABLES = (
    "case_participants",
    "case_event_links",
    "criminal_cases",
)

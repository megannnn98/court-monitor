"""The investigation's explicit testing API for the evaluation and tests.

Not for production code (`tests/test_data_ownership.py` holds every production module
to it; ADR 0031, «Evaluation»).
"""

from __future__ import annotations

# The tables a disposable test or evaluation database is wiped of between runs
# (`db.maintenance.truncate_disposable_tables`); a cache of model answers worth keeping
# is not among them.
DISPOSABLE_TABLES = ("investigation_runs",)

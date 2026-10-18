"""The orchestrator's explicit testing API: reads of its tables for the evaluation and
tests.

Not for production code (`tests/test_data_ownership.py` holds every production module
to it; ADR 0031, «Evaluation»).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection

from orchestrator.storage.models import MonitoringRunRecord


def stage_metrics(connection: Connection) -> list[dict[str, Any] | None]:
    """The stage metrics of every monitoring run."""
    return [metrics for (metrics,) in connection.execute(select(MonitoringRunRecord.stage_metrics))]

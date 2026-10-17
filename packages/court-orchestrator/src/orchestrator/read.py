"""Monitoring and operation facts for readers outside the orchestrator.

The caller supplies its transaction; these reads never commit or mutate runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.models import MonitoringRunStatus
from orchestrator.storage.models import MonitoringRunRecord, OperatorOperationRunRecord


@dataclass(frozen=True)
class LiveOperation:
    mode: str | None


def live_operation(session: Session) -> LiveOperation | None:
    """Latest pending/running operation, including operations with no mode parameter."""
    row = session.execute(
        select(OperatorOperationRunRecord.parameters["mode"].astext)
        .where(OperatorOperationRunRecord.status.in_(("pending", "running")))
        .order_by(OperatorOperationRunRecord.id.desc())
        .limit(1)
    ).first()
    return None if row is None else LiveOperation(mode=row[0])


def latest_monitoring_status(session: Session) -> str | None:
    """The latest monitoring run's status, or None before the first run."""
    return session.scalar(
        select(MonitoringRunRecord.status).order_by(MonitoringRunRecord.started_at.desc()).limit(1)
    )


def last_successful_operation_at(session: Session) -> datetime | None:
    """Start time of the last successful operation by run id, normalized to UTC."""
    started = session.scalar(
        select(OperatorOperationRunRecord.started_at)
        .where(OperatorOperationRunRecord.status == "succeeded")
        .order_by(OperatorOperationRunRecord.id.desc())
        .limit(1)
    )
    if started is None:
        return None
    return started if started.tzinfo is not None else started.replace(tzinfo=UTC)


def stale_monitoring_runs(session: Session, *, stale_after: timedelta) -> int:
    """Running runs whose heartbeat is older than the configured interval."""
    return (
        session.scalar(
            select(func.count(MonitoringRunRecord.id)).where(
                MonitoringRunRecord.status == MonitoringRunStatus.RUNNING.value,
                MonitoringRunRecord.heartbeat_at < func.now() - stale_after,
            )
        )
        or 0
    )

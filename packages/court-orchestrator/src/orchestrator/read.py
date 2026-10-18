"""Monitoring and operation facts for readers outside the orchestrator.

The caller supplies its transaction; these reads never commit or mutate runs.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from orchestrator.models import MonitoringRunStatus
from orchestrator.operations import OperationRunStatus
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


def sources_running_at(session: Session, sources: Collection[str], moment: datetime) -> set[str]:
    """Of the sources asked for, those a monitoring run held at `moment`: begun before it
    and not finished by then."""
    if not sources:
        return set()
    return {
        source
        for source in session.scalars(
            select(MonitoringRunRecord.source).where(
                MonitoringRunRecord.source.in_(list(sources)),
                MonitoringRunRecord.started_at < moment,
                or_(
                    MonitoringRunRecord.finished_at.is_(None),
                    MonitoringRunRecord.finished_at > moment,
                ),
            )
        ).all()
        if source is not None
    }


@dataclass(frozen=True)
class SourceError:
    """A failed item of a monitoring run: a load's parsing or network error."""

    source: str | None
    stage: str | None
    error_type: str | None
    # The first 240 characters of the message.
    message: str | None
    created_at: datetime


_SOURCE_ERRORS = text(
    """
    SELECT r.source, i.stage, i.error_type, left(i.error_message, 240) AS message, i.created_at
    FROM monitoring_run_items i JOIN monitoring_runs r ON r.id = i.run_id
    WHERE i.status = 'failed'
    ORDER BY i.created_at DESC, i.id DESC
    LIMIT :limit
    """
)
_RECENT_SOURCE_ERRORS = text(
    """
    SELECT count(DISTINCT r.source)
    FROM monitoring_run_items i JOIN monitoring_runs r ON r.id = i.run_id
    WHERE i.status = 'failed' AND i.created_at >= now() - interval '7 days'
    """
)


def source_errors(session: Session, *, limit: int) -> list[SourceError]:
    """The latest failed items of the monitoring runs, the latest first."""
    return [SourceError(*row) for row in session.execute(_SOURCE_ERRORS, {"limit": limit})]


def recent_source_error_count(session: Session) -> int:
    """The sources that failed to load in the last week."""
    return session.scalar(_RECENT_SOURCE_ERRORS) or 0


@dataclass(frozen=True)
class OperationState:
    """The latest run of an operator operation: its parameters and how it stands."""

    parameters: dict[str, Any]
    status: OperationRunStatus
    created_at: datetime
    heartbeat_at: datetime | None


def latest_operation(session: Session, name: str) -> OperationState | None:
    """The latest run of the operator operation `name`, or None before the first."""
    record = session.scalars(
        select(OperatorOperationRunRecord)
        .where(OperatorOperationRunRecord.operation_name == name)
        .order_by(OperatorOperationRunRecord.id.desc())
        .limit(1)
    ).first()
    if record is None:
        return None
    return OperationState(
        dict(record.parameters),
        OperationRunStatus(record.status),
        record.created_at,
        record.heartbeat_at,
    )

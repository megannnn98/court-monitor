"""The orchestrator's tables: monitoring runs, their failed items, each source's checkpoint, operator operation runs.

Internal: only this package reads and writes them; others go through its public
API (docs/architecture/data-ownership.md). Every class registers its table on
`db.models.base.Base`; `migrations/model_registry.py` composes them for Alembic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class MonitoringRunRecord(Base):
    """One automated monitoring run (ADR 0013): orchestration state, not domain data."""

    __tablename__ = "monitoring_runs"
    __table_args__ = (
        Index("ix_monitoring_runs_started_at", "started_at"),
        # A source's latest runs first (`orchestrator.source_health`).
        Index(
            "ix_monitoring_runs_source_started_at",
            "source",
            text("started_at DESC"),
            text("id DESC"),
            postgresql_where=text("source IS NOT NULL"),
        ),
        Index("ix_monitoring_runs_status", "status"),
        # At most one live run per scope ("source:<name>" or "derived"), across processes.
        Index(
            "uq_monitoring_runs_running_scope",
            "scope",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    documents_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_ingested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    articles_extracted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    events_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    persons_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    persons_linked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    person_reviews_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classifications_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rf_matches_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    semantic_entities_indexed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    findings_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rf_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("rosfinmonitoring_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    # Per-stage breakdowns (status counts, durations, skip reasons).
    stage_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MonitoringRunItemRecord(Base):
    """A single object that failed inside a monitoring run stage."""

    __tablename__ = "monitoring_run_items"
    __table_args__ = (Index("ix_monitoring_run_items_run_id", "run_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("monitoring_runs.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    error_type: Mapped[str] = mapped_column(String(255), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceMonitoringStateRecord(Base):
    """Per-source checkpoint of regular monitoring (backfill and dry-run never touch it)."""

    __tablename__ = "source_monitoring_state"

    source_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_successful_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitoring_runs.id", ondelete="SET NULL"), nullable=True
    )
    last_successful_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_discovered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Only for sources with a real upstream cursor; HTML listings have none.
    last_external_marker: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OperatorOperationRunRecord(Base):
    """One run of a routine operation started from the operator console.

    The database is the source of truth: every API process sees the same runs, and a
    restart keeps the history. At most one live run per operation, across processes.
    """

    __tablename__ = "operator_operation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'interrupted')",
            name="ck_operator_operation_runs_status",
        ),
        Index("ix_operator_operation_runs_created_at", "created_at"),
        Index(
            "uq_operator_operation_runs_active_operation",
            "operation_name",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_name: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    command: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    return_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    stderr: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

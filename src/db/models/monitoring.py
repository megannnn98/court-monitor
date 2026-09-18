"""Automated monitoring runs, items, source checkpoints and findings (ADR 0013)."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
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


class MonitoringFindingRecord(Base):
    """An actionable monitoring result about a person; not a Person status."""

    __tablename__ = "monitoring_findings"
    __table_args__ = (
        UniqueConstraint(
            "finding_type",
            "person_id",
            "criteria_version",
            name="uq_monitoring_findings_type_person_criteria",
        ),
        Index("ix_monitoring_findings_first_seen_run", "first_seen_run_id"),
        Index("ix_monitoring_findings_active", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_type: Mapped[str] = mapped_column(String(64), nullable=False)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), nullable=False
    )
    criteria_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # False once the person stopped satisfying the criterion; history is kept.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    first_seen_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitoring_runs.id", ondelete="SET NULL"), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitoring_runs.id", ondelete="SET NULL"), nullable=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    inactive_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Provenance at last sighting; evidence and sources are reachable through the person.
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("rosfinmonitoring_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    persecution_classification_id: Mapped[int | None] = mapped_column(
        ForeignKey("persecution_classifications.id", ondelete="SET NULL"), nullable=True
    )
    rosfin_match_id: Mapped[int | None] = mapped_column(
        ForeignKey("rosfin_matches.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

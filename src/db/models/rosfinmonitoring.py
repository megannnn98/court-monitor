"""Rosfinmonitoring list snapshots, entries and person matches."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class RosfinmonitoringSnapshotRecord(Base):
    __tablename__ = "rosfinmonitoring_snapshots"
    __table_args__ = (
        Index("ix_rosfinmonitoring_snapshots_snapshot_date", "snapshot_date"),
        UniqueConstraint("content_hash", name="uq_rosfinmonitoring_snapshots_content_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RosfinmonitoringEntryRecord(Base):
    __tablename__ = "rosfinmonitoring_entries"
    __table_args__ = (
        Index("ix_rosfinmonitoring_entries_snapshot_id", "snapshot_id"),
        Index("ix_rosfinmonitoring_entries_matching_key", "matching_key"),
        Index("ix_rosfinmonitoring_entries_normalized_name", "normalized_name"),
        # The matcher's `ILIKE '%stem%'` candidate fetch (pg_trgm).
        Index(
            "ix_rosfinmonitoring_entries_normalized_name_trgm",
            "normalized_name",
            postgresql_using="gin",
            postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
        UniqueConstraint(
            "snapshot_id",
            "full_name",
            "birth_date",
            name="uq_rosfinmonitoring_entries_snapshot_name_birth",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("rosfinmonitoring_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False)
    matching_key: Mapped[str] = mapped_column(String(255), nullable=False)
    birth_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    birth_place: Mapped[str | None] = mapped_column(String(512), nullable=True)
    snils: Mapped[str | None] = mapped_column(String(20), nullable=True)
    inn: Mapped[str | None] = mapped_column(String(20), nullable=True)
    inclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    inclusion_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RosfinMatchRecord(Base):
    """ORM model for storing Person ↔ Rosfinmonitoring match results."""

    __tablename__ = "rosfin_matches"
    __table_args__ = (
        Index("ix_rosfin_matches_person_id", "person_id"),
        Index("ix_rosfin_matches_snapshot_id", "snapshot_id"),
        Index("ix_rosfin_matches_status", "status"),
        UniqueConstraint(
            "person_id",
            "snapshot_id",
            name="uq_rosfin_matches_person_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("rosfinmonitoring_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    matched_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("rosfinmonitoring_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    matched_entry_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    candidate_entries: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # NULL: written before the matcher version was recorded (treated as outdated).
    matcher_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    matcher_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

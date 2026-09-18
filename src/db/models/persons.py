"""Canonical persons, aliases, merges, reviews and entity-resolution decisions."""

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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class PersonRecord(Base):
    __tablename__ = "persons"
    __table_args__ = (
        # Candidate lookup only: namesakes may share a matching_key (ADR 0012).
        Index(
            "ix_persons_matching_key_active",
            "matching_key",
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_persons_status", "status"),
        # ER v2 fuzzy candidate narrowing (pg_trgm).
        Index(
            "ix_persons_normalized_name_trgm",
            "normalized_name",
            postgresql_using="gin",
            postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    matching_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )


class PersonAliasRecord(Base):
    __tablename__ = "person_aliases"
    __table_args__ = (
        Index("ix_person_aliases_person_id", "person_id"),
        Index("ix_person_aliases_matching_key", "matching_key"),
        Index(
            "ix_person_aliases_normalized_text_trgm",
            "normalized_text",
            postgresql_using="gin",
            postgresql_ops={"normalized_text": "gin_trgm_ops"},
        ),
        UniqueConstraint(
            "person_id",
            "surface_text",
            name="uq_person_aliases_person_surface",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
    )
    surface_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    matching_key: Mapped[str] = mapped_column(String(255), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_mention_id: Mapped[int | None] = mapped_column(
        ForeignKey("entity_mentions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PersonMergeRecord(Base):
    __tablename__ = "person_merges"
    __table_args__ = (Index("ix_person_merges_target", "target_person_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewRecordModel(Base):
    __tablename__ = "review_records"
    __table_args__ = (
        Index("ix_review_records_subject", "subject_type", "subject_id"),
        # At most one pending review per subject (idempotent review tasks).
        Index(
            "uq_review_records_pending_subject",
            "subject_type",
            "subject_id",
            unique=True,
            postgresql_where=text("decision = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class PersonResolutionDecisionRecord(Base):
    """ER v2 provenance per mention; a pending review keeps the mention unlinked."""

    __tablename__ = "person_resolution_decisions"
    __table_args__ = (
        UniqueConstraint(
            "mention_id",
            "resolver_version",
            name="uq_person_resolution_decisions_mention_version",
        ),
        Index("ix_person_resolution_decisions_status", "status"),
        Index("ix_person_resolution_decisions_selected_person", "selected_person_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mention_id: Mapped[int] = mapped_column(
        ForeignKey("entity_mentions.id", ondelete="CASCADE"), nullable=False
    )
    resolver_version: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    selected_person_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id", ondelete="SET NULL"), nullable=True
    )
    resolution_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasons: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    identity: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    semantic_source: Mapped[str] = mapped_column(String(32), nullable=False)
    review_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # KEEP_SEPARATE: the reviewer decided the selected person is not this one.
    distinct_from_person_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PersonEventLinkRecord(Base):
    __tablename__ = "person_event_links"
    __table_args__ = (
        Index("ix_person_event_links_person_id", "person_id"),
        Index("ix_person_event_links_event_id", "event_id"),
        UniqueConstraint(
            "person_id",
            "event_id",
            "role",
            name="uq_person_event_links_person_event_role",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

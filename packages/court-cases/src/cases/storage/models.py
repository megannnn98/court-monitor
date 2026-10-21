"""The criminal cases and the evidence that ties extracted events to them.

Internal: only this package reads and writes them; others go through its public API
(docs/architecture/data-ownership.md). Processing replaces `extracted_events` rows and
renumbers `entity_groups` on every rebuild, so a link is anchored on `evidence_key` (made
of the article and the quote, which survive a rebuild) and a participant on
`entity_groups.key`, never on the integer ids.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class CriminalCaseRecord(Base):
    """One criminal case as the news tell it. The id never changes once given."""

    __tablename__ = "criminal_cases"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # True until something firmer than the news tells it (a case number) is known.
    preliminary: Mapped[bool] = mapped_column(Boolean, nullable=False)
    case_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)


class CaseEventLinkRecord(Base):
    """One extracted event's tie to a case, with what the tie rests on."""

    __tablename__ = "case_event_links"
    __table_args__ = (
        Index("ix_case_event_links_case_id", "case_id"),
        Index("ix_case_event_links_article_id", "article_id"),
        Index("ix_case_event_links_case_number", "case_number"),
        Index("ix_case_event_links_targets", "targets", postgresql_using="gin"),
    )

    # sha256 of the article, the event type, the quote and its place among equal ones.
    evidence_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("criminal_cases.id"), nullable=False)
    # SET NULL keeps the link when processing replaces its events; the next run re-attaches.
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("extracted_events.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    # The publication the event was found in.
    article_id: Mapped[int] = mapped_column(
        ForeignKey("parsed_articles.id", ondelete="CASCADE"), nullable=False
    )
    # False once the event or its article is gone or no longer names the person.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # candidate | confirmed | rejected. A link made by the rules is confirmed; a
    # candidate is one a person has to look at; rejected is a rolled back link.
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="confirmed")
    # Who rolled the link back, when, and why: the record of the decision.
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # `entity_groups.key` of the people the event is about (never judges or lawyers).
    targets: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    # The event as it was scored (types, dates, charges, places), to rescore without re-reading.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    # Features the tie rests on; empty for the event that opened its case.
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    # Cases that matched but were not joined: id, score, features, vetoes.
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CaseParticipantRecord(Base):
    __tablename__ = "case_participants"
    __table_args__ = (Index("ix_case_participants_person_key", "person_key"),)

    # The link whose event names the person: the supporting evidence of the participation.
    evidence_key: Mapped[str] = mapped_column(
        ForeignKey("case_event_links.evidence_key", ondelete="CASCADE"), primary_key=True
    )
    # `entity_groups.key`, resolved to the group on read.
    person_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    # «figurant» only when the roles step says so; None otherwise.
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)


class EventClusterRecord(Base):
    """One event told by several publications.

    The anchor is the event the cluster was created for: a later publication joins the
    cluster only when it matches the anchor itself, never through a chain of members.
    Ids of events and groups change on a rebuild, so the anchor and the members are
    anchored on `evidence_key` (the article, the step and the words) and on
    `entity_groups.key`.
    """

    __tablename__ = "event_clusters"
    __table_args__ = (
        Index("ix_event_clusters_core_evidence_key", "core_evidence_key"),
        Index("ix_event_clusters_status", "status"),
        Index("ix_event_clusters_targets", "targets", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    # The event the cluster was created for.
    core_evidence_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # The publication the anchor was found in.
    core_article_id: Mapped[int] = mapped_column(
        ForeignKey("parsed_articles.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # `entity_groups.key` of the people the anchor is about.
    targets: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    # The anchor as it was scored, to rescore without re-reading.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # The event's own day; None when the text names none.
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # candidate (a person has to look at it) | confirmed | rejected.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    # The pieces of evidence the cluster rests on.
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    # The clusters the anchor matched but was not joined to: id, score, features, vetoes.
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # False once the cluster was rolled back; the history stays in the members.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EventClusterMemberRecord(Base):
    """One event's place in a cluster, with what the place rests on.

    A member with no cluster is an event that joined nothing and only has candidates:
    an article without a name, which is never put into a cluster on its own.
    """

    __tablename__ = "event_cluster_members"
    __table_args__ = (
        Index("ix_event_cluster_members_cluster_id", "cluster_id"),
        Index("ix_event_cluster_members_status", "status"),
    )

    # sha256 of the article, the event type, the quote and its place among equal ones.
    evidence_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    # The event the membership is about; SET NULL keeps the membership when processing
    # replaces its events.
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("extracted_events.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    # None: the event joined no cluster and has candidates only.
    cluster_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("event_clusters.id", ondelete="CASCADE"), nullable=True
    )
    # core (the anchor) | member | candidate.
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # candidate | confirmed | rejected.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    # The clusters the event matched but was not joined to.
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # False once the membership was rolled back; the row and its decision stay.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    # Who decided, when, and why: the record of a rollback.
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

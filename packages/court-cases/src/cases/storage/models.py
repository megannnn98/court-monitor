"""The criminal cases and the evidence that ties extracted events to them.

Internal: only this package reads and writes them; others go through its public API
(docs/architecture/data-ownership.md). Processing replaces `extracted_events` rows and
renumbers `entity_groups` on every rebuild, so a link is anchored on `evidence_key` (made
of the article and the quote, which survive a rebuild) and a participant on
`entity_groups.key`, never on the integer ids.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, Text, Uuid, func
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

"""The results' tables: monitoring findings, the operator's done marks, the questions asked of «Спросить».

Internal: only this package reads and writes them; others go through its public
API (docs/architecture/data-ownership.md). Every class registers its table on
`db.models.base.Base`; `migrations/model_registry.py` composes them for Alembic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class EntityDoneMarkRecord(Base):
    """The operator's «обработано» on a person of the result: she has dealt with them, and
    the list stops showing them. By key, so it survives every rebuild.

    `news_at` is the person's latest news at the moment of the mark. A later news brings
    the person back: what was dealt with is what was known then, not whatever comes next.
    """

    __tablename__ = "entity_done_marks"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    news_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
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


class ChatQuestionRecord(Base):
    """A question asked on «Спросить» (`results.ask`): what was asked, which counts the
    model called for, what they gave, what it answered and what it cost."""

    __tablename__ = "chat_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    asked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # [{call, result}, …]: each count the model called for with what it gave.
    calls: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    # answered / refused / failed.
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)

"""The investigation's table: stored, resumable runs (ADR 0027).

Internal: only this package reads and writes them; others go through its public
API (docs/architecture/data-ownership.md). Every class registers its table on
`db.models.base.Base`; `migrations/model_registry.py` composes them for Alembic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class InvestigationRunRecord(Base):
    __tablename__ = "investigation_runs"
    __table_args__ = (Index("ix_investigation_runs_created_at", "created_at"),)

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # running, then the result's status.
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    scenario: Mapped[str | None] = mapped_column(String(32), nullable=True)
    round: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # The state to resume from; None once finished.
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    failure: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

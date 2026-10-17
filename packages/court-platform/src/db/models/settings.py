"""Settings the operator changes in the console, and what the entity model spent.

`app_settings` keeps a value per key: the console writes it, every process reads it, a
restart keeps it, and no `.env` needs a change. `entity_model_spend` is the ledger of the
entity model's calls (`llm.model_choice`): one row per model per step run (or per test of
a model), never the junk screen's Jev.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class AppSettingRecord(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EntityModelSpendRecord(Base):
    """What one step run (`command`) or one test of a model spent on one model.

    `cost_usd` is what OpenRouter reported. Calls it reported no cost for are counted
    apart (`uncosted_*`): the console estimates them by the price list and says so."""

    __tablename__ = "entity_model_spend"
    __table_args__ = (Index("ix_entity_model_spend_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    command: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    calls: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    uncosted_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    uncosted_prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    uncosted_completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

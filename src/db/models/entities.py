"""Person entities gathered from mentions (`entities.grouping`). Derived and rebuilt whole."""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class EntityGroupRecord(Base):
    """One person as the news wrote it: a given name and a surname, however declined."""

    __tablename__ = "entity_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    # «имя фамилия» folded and without case endings; stable across rebuilds, used in URLs.
    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # [[form, mentions], …], most frequent first.
    variants: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False)
    article_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # {event_type: events}, the case events the mentions take part in.
    event_types: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    last_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # male / female; None when unknown.
    gender: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # rules: the grouping rules named it; model: a language model gave the nominative.
    name_source: Mapped[str] = mapped_column(String(8), nullable=False, server_default="rules")
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EntityGroupMentionRecord(Base):
    __tablename__ = "entity_group_mentions"
    __table_args__ = (Index("ix_entity_group_mentions_mention_id", "mention_id"),)

    group_id: Mapped[int] = mapped_column(
        ForeignKey("entity_groups.id", ondelete="CASCADE"), primary_key=True
    )
    mention_id: Mapped[int] = mapped_column(
        ForeignKey("entity_mentions.id", ondelete="CASCADE"), primary_key=True
    )


class EntityNameNormalizationRecord(Base):
    """What a model answered for an entity key: reused by every later rebuild.

    Keyed by the rule-based entity key and the prompt version: a new prompt asks again."""

    __tablename__ = "entity_name_normalizations"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    prompt_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    nominative: Mapped[str] = mapped_column(Text, nullable=False)
    gender: Mapped[str] = mapped_column(String(8), nullable=False)
    is_person: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

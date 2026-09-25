"""Person entities gathered from mentions (`entities.grouping`). Derived and rebuilt whole."""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, text
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
    # rules: the grouping rules named it; model: a language model gave the nominative;
    # manual: a person corrected it.
    name_source: Mapped[str] = mapped_column(String(8), nullable=False, server_default="rules")
    # [[region, publications], …] from registry cards, most named first; the news give none.
    regions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
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


class EntityGroupChargeRecord(Base):
    """A Criminal Code article an event ties to the entity: it is the event's target and
    the article its legal basis. One row per event and article; rebuilt with the groups."""

    __tablename__ = "entity_group_charges"
    __table_args__ = (
        Index("ix_entity_group_charges_group_id", "group_id"),
        Index("ix_entity_group_charges_article", "article"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("entity_groups.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_events.id", ondelete="CASCADE"), nullable=False
    )
    # The publication (parsed article) the event was found in.
    publication_id: Mapped[int] = mapped_column(
        ForeignKey("parsed_articles.id", ondelete="CASCADE"), nullable=False
    )
    # «205.2», its part and clause when the text gives them.
    article: Mapped[str] = mapped_column(String(32), nullable=False)
    part: Mapped[str | None] = mapped_column(String(16), nullable=True)
    clause: Mapped[str | None] = mapped_column(String(16), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Targets of the event that are other people: above zero the article is shared, and
    # the extractor makes everyone in the sentence a target (lawyers and judges too).
    other_targets: Mapped[int] = mapped_column(Integer, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)


class EntityGroupRfMatchRecord(Base):
    """A person of the Rosfinmonitoring list an entity may be: by name, without a birth
    date (the news give none). Rewritten by every «Сверить с Росфинмониторингом»."""

    __tablename__ = "entity_group_rf_matches"
    __table_args__ = (Index("ix_entity_group_rf_matches_group_id", "group_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("entity_groups.id", ondelete="CASCADE"), nullable=False
    )
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("rosfinmonitoring_entries.id", ondelete="CASCADE"), nullable=False
    )
    # full: given name, patronymic and surname are the list's; name: given name and
    # surname, one side without a patronymic — maybe a namesake.
    level: Mapped[str] = mapped_column(String(8), nullable=False)


class EntityGroupRoleRecord(Base):
    """Whether a criminal case is opened against the entity, or it is only mentioned.

    Rewritten by every «Найти фигурантов»; the entities on the Rosfinmonitoring list
    have none."""

    __tablename__ = "entity_group_roles"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("entity_groups.id", ondelete="CASCADE"), primary_key=True
    )
    # figurant: a suspect, accused, defendant or convict of a criminal case; possible:
    # detained or searched with no case named; mentioned: someone else; unclear.
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # The model's finer role («lawyer», «judge», …); None for the rules.
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # article: the rules (sole target of an event with a УК article); model.
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)


class EntityRoleAnswerRecord(Base):
    """A model's answer about an entity's role, for these very quotes: reused until the
    quotes or the prompt change."""

    __tablename__ = "entity_role_answers"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    input_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    prompt_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EntityPairDecisionRecord(Base):
    """A person's decision on two entities that may be one person: «same» merges them,
    now and at every rebuild; «different» takes the pair off «Спорные случаи».

    Keyed by the entity keys, which survive a rebuild; `key_a` sorts before `key_b`."""

    __tablename__ = "entity_pair_decisions"

    key_a: Mapped[str] = mapped_column(String(255), primary_key=True)
    key_b: Mapped[str] = mapped_column(String(255), primary_key=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    # manual: a person decided on «Спорные случаи»; rf: one side is on the
    # Rosfinmonitoring list, so the pair is taken for one person without asking.
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="manual")
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EntityGroupPoliticsRecord(Base):
    """Whether a figurant's criminal case is political persecution or common crime.

    Rewritten by every «Отобрать политические дела»; only figurants off the
    Rosfinmonitoring list have one."""

    __tablename__ = "entity_group_politics"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("entity_groups.id", ondelete="CASCADE"), primary_key=True
    )
    # political, criminal or unclear.
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    # article: a political article of the Criminal Code; model: a model read the quotes.
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)


class EntityPoliticsAnswerRecord(Base):
    """A model's verdict on a figurant's case for this very input: reused until the
    input or the prompt changes."""

    __tablename__ = "entity_politics_answers"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    input_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    prompt_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EntityOfficialMarkRecord(Base):
    """A person's word on whether an entity is an official (judge, prosecutor, …): it
    overrides what the texts and the model say, and survives every rebuild."""

    __tablename__ = "entity_official_marks"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    official: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EntityNameOverrideRecord(Base):
    """A person's correction of an entity's name («Лидия Мониава», not «Лида»): it
    wins over the rules and the model, and survives every rebuild."""

    __tablename__ = "entity_name_overrides"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
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

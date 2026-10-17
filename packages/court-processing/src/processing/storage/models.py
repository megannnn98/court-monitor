"""The processing's tables: extraction runs, mentions and events; entity groups, their roles, names and a person's decisions on them; sentence and digest evidence; the junk screen's holds.

Internal: only this package reads and writes them; others go through its public
API (docs/architecture/data-ownership.md). Every class registers its table on
`db.models.base.Base`; `migrations/model_registry.py` composes them for Alembic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class EntityGroupUnnamedMentionRecord(Base):
    __tablename__ = "entity_group_unnamed_mentions"
    __table_args__ = (Index("ix_entity_group_unnamed_mentions_figurant_key", "figurant_key"),)

    group_id: Mapped[int] = mapped_column(
        ForeignKey("entity_groups.id", ondelete="CASCADE"), primary_key=True
    )
    figurant_key: Mapped[str] = mapped_column(String(64), primary_key=True)


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
    # In the key: an answer is one model's, never another model's (16.10.2026).
    model: Mapped[str] = mapped_column(String(100), primary_key=True)
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


class EntityOfficialMarkRecord(Base):
    """A person's word on whether an entity is an official (judge, prosecutor, …): it
    overrides what the texts and the model say, and survives every rebuild."""

    __tablename__ = "entity_official_marks"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    official: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EntityRoleDecisionRecord(Base):
    """A person's word on an entity's role in a case (figurant, possible, mentioned):
    it overrides step 4 and survives every rebuild, as a name correction does."""

    __tablename__ = "entity_role_decisions"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    # figurant, possible or mentioned; «unclear» is never decided, only left to be redone.
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # onupdate as well as server_default: re-deciding must move the moment, so that a
    # record's own timestamp says which of two rows is the later word.
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
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


class EntityRemovalRecord(Base):
    """A person's word that an entity is nobody («Дмитрий Путин», made of two people's
    names): it is left out of every rebuild. The name is the one it had when removed, to
    tell the removals apart in their list."""

    __tablename__ = "entity_removals"

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
    # In the key: an answer is one model's, never another model's (16.10.2026).
    model: Mapped[str] = mapped_column(String(100), primary_key=True)
    nominative: Mapped[str] = mapped_column(Text, nullable=False)
    gender: Mapped[str] = mapped_column(String(8), nullable=False)
    is_person: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ArticleExtractionRunRecord(Base):
    __tablename__ = "article_extraction_runs"
    __table_args__ = (
        UniqueConstraint(
            "article_id",
            "article_content_hash",
            "extractor_name",
            "extractor_version",
            "normalizer_version",
            name="uq_article_extraction_runs_version",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("parsed_articles.id", ondelete="CASCADE"),
        nullable=False,
    )
    article_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EntityMentionRecord(Base):
    __tablename__ = "entity_mentions"
    __table_args__ = (
        UniqueConstraint(
            "extraction_run_id",
            "entity_type",
            "start_offset",
            "end_offset",
            name="uq_entity_mentions_run_type_span",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    extraction_run_id: Mapped[int] = mapped_column(
        ForeignKey("article_extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    surface_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    normalized_data: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    extractor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractedEventRecord(Base):
    __tablename__ = "extracted_events"
    # From an event back to its article for person-event views and exports.
    __table_args__ = (Index("ix_extracted_events_extraction_run_id", "extraction_run_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    extraction_run_id: Mapped[int] = mapped_column(
        ForeignKey("article_extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    attributes: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    extractor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventEntityMentionRecord(Base):
    __tablename__ = "event_entity_mentions"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "mention_id",
            "role",
            name="uq_event_entity_mentions_event_mention_role",
        ),
    )

    event_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mention_id: Mapped[int] = mapped_column(
        ForeignKey("entity_mentions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(64), primary_key=True)


class JunkScreenHoldRecord(Base):
    """An article the junk purge would delete (no criminal-case event extracted) that the
    embedding screen held back for a person to look at (`processing.junk.screen`).

    `held`: kept out of every purge until a person decides; `junk`: a person said it is
    junk, the next purge deletes it (and this row with it); `released`: a model read a
    case in it and wrote the event the rules missed (`processing.junk.hold_reader`). An article
    a new extraction found the event of loses the row.
    """

    __tablename__ = "junk_screen_holds"

    article_id: Mapped[int] = mapped_column(
        ForeignKey("parsed_articles.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="held")
    score: Mapped[float] = mapped_column(Float, nullable=False)
    cutoff: Mapped[float] = mapped_column(Float, nullable=False)
    screen: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # What a person or a new extraction said last.
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # What a model read of the article (`processing.junk.hold_reader`): who read, `case` (the
    # hold is then `released`) or `junk` (left to a person), and the event it named.
    reader: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reader_verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reader_event: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ArticleDigestAnswerRecord(Base):
    """A model's verdict on whether an article's title substantively describes a
    person's own case (an arrest, a sentence, a new charge), not a roundup of several
    people, a foreign-agent/undesirable listing, a support rally, or the like: cached by
    article id, asked once regardless of how many figurants' evidence points to it."""

    __tablename__ = "article_digest_answers"

    article_id: Mapped[int] = mapped_column(
        ForeignKey("parsed_articles.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    relevant: Mapped[bool] = mapped_column(Boolean, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArticleSentenceReadingRecord(Base):
    """That a model read an article for court sentences, and by which prompt: an article
    it found none in is not read again."""

    __tablename__ = "article_sentence_readings"

    article_id: Mapped[int] = mapped_column(
        ForeignKey("parsed_articles.id", ondelete="CASCADE"), primary_key=True
    )
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArticleSentenceRecord(Base):
    """One person's sentence as one article tells it (`processing.evidence.sentences`): where, what
    punishment, for what. The same sentence told by five articles is five rows;
    `results.queries.sentence_cases` folds them into one case."""

    __tablename__ = "article_sentences"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("parsed_articles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The name or the description, as the model gave it.
    person: Mapped[str] = mapped_column(Text, nullable=False)
    # «фамилия имя» folded, when the article gives both; None for a person it does not name.
    person_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # A subject of `processing.evidence.regions`; «» when the article does not tell.
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # 0: not told.
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    fine_rub: Mapped[int] = mapped_column(BigInteger, nullable=False)
    in_absentia: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # «2026-09-24», «2024-03», «2024» or «»: as exact as the article is.
    sentenced_on: Mapped[str] = mapped_column(String(10), nullable=False)
    articles: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    # A person said the row is wrong: it is in no count.
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class ExcludedPersonRecord(Base):
    """An official, and anyone else who must never become a target figurant.

    Step 4 reads the active rows and treats such a person as named in a case, never as
    the one it is opened against.

    The list is called «Должностные лица» everywhere a person sees it — on
    «Справочники», in `officials.csv`, in `AIRTABLE_OFFICIALS_TABLE`. The table kept the
    name it was created under: renaming it needs a migration, and the working tree holds
    another session's uncommitted work in the very files that carry the table name, which
    a commit here would have swept in. So the name is stale on purpose, not forgotten.
    """

    __tablename__ = "excluded_persons"
    __table_args__ = (Index("ix_excluded_persons_normalized_name", "normalized_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # The Airtable record id: the one stable link between the two systems.
    external_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False)
    # judge / prosecutor / police / official / lawyer / witness / other.
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="other")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

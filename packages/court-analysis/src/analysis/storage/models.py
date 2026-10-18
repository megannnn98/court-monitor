"""The analysis's tables: politics and case news verdicts, the Rosfinmonitoring list and its matches, unnamed figurants, persecution classifications, the operator's reference lists.

Internal: only this package reads and writes them; others go through its public
API (docs/architecture/data-ownership.md). Every class registers its table on
`db.models.base.Base`; `migrations/model_registry.py` composes them for Alembic.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
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


class EntityGroupPoliticsRecord(Base):
    """Whether a figurant's criminal case is political persecution or common crime.

    Rewritten by every «Отобрать политические дела»; every figurant has one, on the
    Rosfinmonitoring list or not."""

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
    # In the key: an answer is one model's, never another model's (16.10.2026).
    model: Mapped[str] = mapped_column(String(100), primary_key=True)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EntityPoliticsDecisionRecord(Base):
    """A person's word on whether an entity's case is political: it overrides step 5 and
    survives every rebuild, as a name correction does."""

    __tablename__ = "entity_politics_decisions"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    # political or criminal; «unclear» is never decided, only left to be redone.
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UnnamedFigurantRecord(Base):
    """A person a publication charges without a name — «17-летний житель Тюмени» — with
    what the text tells of them, to be matched with the Rosfinmonitoring list.

    Derived: rewritten by every search (`analysis.unnamed.figurants`). `key` is the hash of the
    publication and the sentence: it survives a rebuild, and the decisions hold to it."""

    __tablename__ = "unnamed_figurants"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("parsed_articles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    # What the text tells: the age, male/female, the town or region, the surname's initial.
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(8), nullable=True)
    place: Mapped[str] = mapped_column(Text, nullable=False)
    initial: Mapped[str | None] = mapped_column(String(4), nullable=True)
    articles: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UnnamedScreenedRecord(Base):
    """An unnamed person the search set aside on its own — a common crime, or one more
    sentence about a person already on a card — kept so that the operator can see who
    was set aside and why, and take them back (`UnnamedKeepRecord`).

    Derived: rewritten by every search beside `unnamed_figurants`, with its columns."""

    __tablename__ = "unnamed_screened"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("parsed_articles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(8), nullable=True)
    place: Mapped[str] = mapped_column(Text, nullable=False)
    initial: Mapped[str | None] = mapped_column(String(4), nullable=True)
    articles: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # common_crime, criminal_motive or duplicate (`analysis.unnamed.figurants`).
    reason: Mapped[str] = mapped_column(String(32), nullable=False)


class UnnamedKeepRecord(Base):
    """A person's word that an unnamed figurant stays on a card whatever the search
    makes of it. Not derived: kept through every search, by the figurant's key."""

    __tablename__ = "unnamed_keeps"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UnnamedAnswerRecord(Base):
    """A model's reading of a sentence about an unnamed person, for this very sentence:
    reused until the sentence or the prompt changes."""

    __tablename__ = "unnamed_answers"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    input_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    prompt_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    # In the key: an answer is one model's, never another model's (16.10.2026).
    model: Mapped[str] = mapped_column(String(100), primary_key=True)
    # The answer as JSON (`analysis.unnamed.figurants.UnnamedAnswer`).
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UnnamedDecisionRecord(Base):
    """A person's word on an unnamed figurant: this entry of the list is them («same»), it
    is not («different»), or none is («none», with an empty candidate).

    Not derived: kept through every search, by the figurant's key and the entry's name
    and birth date (an entry's id changes with every snapshot of the list)."""

    __tablename__ = "unnamed_decisions"

    figurant_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate: Mapped[str] = mapped_column(String(255), primary_key=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UnnamedIdentityResolutionRecord(Base):
    """A person's identification of an unnamed figurant, kept by the figurant key.

    The link to rebuilt entity groups is materialized by step 3 in
    `entity_group_unnamed_mentions`; this row is the stable manual decision.
    """

    __tablename__ = "unnamed_identity_resolutions"

    figurant_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    resolution: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    existing_person_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rf_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    rf_birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="manual")
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EntityGroupNewsRecord(Base):
    """What a political case's latest news is: a new case, a sentence, or more of an old
    one — the operator adds the new ones and the sentences first.

    Rewritten by every «Отобрать политические дела»; only the political have one."""

    __tablename__ = "entity_group_news"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("entity_groups.id", ondelete="CASCADE"), primary_key=True
    )
    # new_case, sentence, ongoing or other.
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # model: a model read the latest quotes; none: no model answered.
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    # The latest publication the answer is about.
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EntityNewsAnswerRecord(Base):
    """A model's reading of a case's latest news, for these very quotes: reused until the
    quotes or the prompt change."""

    __tablename__ = "entity_news_answers"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    input_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    prompt_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    # In the key: an answer is one model's, never another model's (16.10.2026).
    model: Mapped[str] = mapped_column(String(100), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PersecutionClassificationRecord(Base):
    __tablename__ = "persecution_classifications"
    __table_args__ = (
        Index("ix_persecution_classifications_person_id", "person_id"),
        Index("ix_persecution_classifications_status", "status"),
        UniqueConstraint(
            "person_id",
            "classifier_name",
            "classifier_version",
            name="uq_persecution_classifications_person_classifier",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    classifier_name: Mapped[str] = mapped_column(String(255), nullable=False)
    classifier_version: Mapped[str] = mapped_column(String(64), nullable=False)
    classified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AirtableKnownPersonRecord(Base):
    """A person already found and checked by hand: «мы этого человека уже знаем».

    Kept apart from `persons` on purpose: that table is the entity resolution's output
    and is rebuilt as a whole, while this list is an operator's own.
    """

    __tablename__ = "airtable_known_persons"
    __table_args__ = (Index("ix_airtable_known_persons_matching_key", "matching_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # The Airtable record id: the one stable link between the two systems.
    external_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    # Folded as the pipeline folds a person's name, so a row can be compared with it.
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False)
    matching_key: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # What the base says of the person beside the name (`airtable.person_details`): each
    # is None where the base has nothing, or has something that is not read.
    gender: Mapped[str | None] = mapped_column(String(16))
    birth_date: Mapped[date | None] = mapped_column(Date)
    region: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    articles: Mapped[str | None] = mapped_column(Text)
    case_opened_on: Mapped[date | None] = mapped_column(Date)
    sentenced_on: Mapped[date | None] = mapped_column(Date)
    court: Mapped[str | None] = mapped_column(Text)
    court_card_url: Mapped[str | None] = mapped_column(Text)
    in_rfm: Mapped[bool | None] = mapped_column(Boolean)
    rfm_included_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # `onupdate` only fires on an UPDATE, so a fresh row needs the server default too.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CriminalArticleRecord(Base):
    """A criminal article worth watching: «статьи, которые нас интересуют».

    A list of numbers, not of people, and it is kept apart from `parsed_articles`, which
    holds the articles the pipeline has already parsed out of a text. This one is what
    the operator decided to look for, before anything was found.

    A list of its own rather than a column on `sources`: the list is about what is
    charged, not about where it was published, and the two change for different reasons
    and by different hands.
    """

    __tablename__ = "criminal_articles"
    __table_args__ = (Index("ix_criminal_articles_article_key", "article_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # The Airtable record id: the one stable link between the two systems.
    external_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # «Статья 159 УК РФ» as written in the list, kept for the person to read.
    article_text: Mapped[str] = mapped_column(String(512), nullable=False)
    # The same folded to digits alone, so «159» finds «ст. 159» and «159 УК РФ».
    article_key: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


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
    # Where the day came from: empty for the ОВД-Инфо copy, `operator` for the operator's
    # own table, which only fills what the first left empty.
    inclusion_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RfmOperatorEntryRecord(Base):
    """A row of the operator's own table of the list: a copy, replaced whole on a read.

    It is not the list. The list is what fedsfm.ru publishes, and only an entry of a
    snapshot may say a person is on it; this table is kept by hand and says two things
    the state does not — the day a row was added, and that a row was later removed."""

    __tablename__ = "rfm_operator_entries"
    __table_args__ = (Index("ix_rfm_operator_entries_normalized_name", "normalized_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    birth_place: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    added_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    removed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(255), nullable=False, default="")


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

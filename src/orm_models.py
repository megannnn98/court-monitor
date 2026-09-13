from datetime import datetime

from sqlalchemy import (
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "external_id",
            name="uq_source_documents_source_id_external_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ParsedArticleRecord(Base):
    __tablename__ = "parsed_articles"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            name="uq_parsed_articles_document_id",
        ),
        Index(
            "ix_parsed_articles_search_vector_gin",
            "search_vector",
            postgresql_using="gin",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('russian'::regconfig, text)",
            persisted=True,
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractedEventRecord(Base):
    __tablename__ = "extracted_events"

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


class PersonRecord(Base):
    __tablename__ = "persons"
    __table_args__ = (
        Index("ix_persons_matching_key", "matching_key"),
        Index("ix_persons_status", "status"),
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
    __table_args__ = (Index("ix_review_records_subject", "subject_type", "subject_id"),)

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

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
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
    text,
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
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
    )
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
        # Candidate lookup only: namesakes may share a matching_key (ADR 0012).
        Index(
            "ix_persons_matching_key_active",
            "matching_key",
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_persons_status", "status"),
        # ER v2 fuzzy candidate narrowing (pg_trgm).
        Index(
            "ix_persons_normalized_name_trgm",
            "normalized_name",
            postgresql_using="gin",
            postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
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
        Index(
            "ix_person_aliases_normalized_text_trgm",
            "normalized_text",
            postgresql_using="gin",
            postgresql_ops={"normalized_text": "gin_trgm_ops"},
        ),
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
    __table_args__ = (
        Index("ix_review_records_subject", "subject_type", "subject_id"),
        # At most one pending review per subject (idempotent review tasks).
        Index(
            "uq_review_records_pending_subject",
            "subject_type",
            "subject_id",
            unique=True,
            postgresql_where=text("decision = 'pending'"),
        ),
    )

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


class PersonResolutionDecisionRecord(Base):
    """ER v2 provenance per mention; a pending review keeps the mention unlinked."""

    __tablename__ = "person_resolution_decisions"
    __table_args__ = (
        UniqueConstraint(
            "mention_id",
            "resolver_version",
            name="uq_person_resolution_decisions_mention_version",
        ),
        Index("ix_person_resolution_decisions_status", "status"),
        Index("ix_person_resolution_decisions_selected_person", "selected_person_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mention_id: Mapped[int] = mapped_column(
        ForeignKey("entity_mentions.id", ondelete="CASCADE"), nullable=False
    )
    resolver_version: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    selected_person_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id", ondelete="SET NULL"), nullable=True
    )
    resolution_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasons: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    identity: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    semantic_source: Mapped[str] = mapped_column(String(32), nullable=False)
    review_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # KEEP_SEPARATE: the reviewer decided the selected person is not this one.
    distinct_from_person_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PersonEventLinkRecord(Base):
    __tablename__ = "person_event_links"
    __table_args__ = (
        Index("ix_person_event_links_person_id", "person_id"),
        Index("ix_person_event_links_event_id", "event_id"),
        UniqueConstraint(
            "person_id",
            "event_id",
            "role",
            name="uq_person_event_links_person_event_role",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SemanticDocumentRecord(Base):
    """Derived retrieval text per canonical entity (ADR 0011). Rebuildable, not a source of truth."""

    __tablename__ = "semantic_documents"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_semantic_documents_entity"),
        Index(
            "ix_semantic_documents_search_vector_gin",
            "search_vector",
            postgresql_using="gin",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    representation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('russian'::regconfig, text)", persisted=True),
        nullable=False,
    )


class MonitoringRunRecord(Base):
    """One automated monitoring run (ADR 0013): orchestration state, not domain data."""

    __tablename__ = "monitoring_runs"
    __table_args__ = (
        Index("ix_monitoring_runs_started_at", "started_at"),
        Index("ix_monitoring_runs_status", "status"),
        # At most one live run per scope ("source:<name>" or "derived"), across processes.
        Index(
            "uq_monitoring_runs_running_scope",
            "scope",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    documents_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_ingested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    articles_extracted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    events_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    persons_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    persons_linked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    person_reviews_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classifications_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rf_matches_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    semantic_entities_indexed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    findings_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rf_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("rosfinmonitoring_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    # Per-stage breakdowns (status counts, durations, skip reasons).
    stage_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MonitoringRunItemRecord(Base):
    """A single object that failed inside a monitoring run stage."""

    __tablename__ = "monitoring_run_items"
    __table_args__ = (Index("ix_monitoring_run_items_run_id", "run_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("monitoring_runs.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    error_type: Mapped[str] = mapped_column(String(255), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceMonitoringStateRecord(Base):
    """Per-source checkpoint of regular monitoring (backfill and dry-run never touch it)."""

    __tablename__ = "source_monitoring_state"

    source_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_successful_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitoring_runs.id", ondelete="SET NULL"), nullable=True
    )
    last_successful_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_discovered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Only for sources with a real upstream cursor; HTML listings have none.
    last_external_marker: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
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

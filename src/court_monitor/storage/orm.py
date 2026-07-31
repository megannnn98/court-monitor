"""SQLAlchemy 2 ORM models.

* ``SourceDocument`` — fetched page/file with provenance + dedup hash.
* ``ExtractedFact``  — every extracted value with status/confidence/quote.
* ``PersonRecord``   — entries from external registries (e.g. Rosfinmonitoring).
* ``ReviewItem``     — generic operator review queue (parser failures, etc.).
* ``AuditLog``       — append-only record of operator decisions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from court_monitor.domain.models import PERSON_NAME_FIELD


def _now_utc() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base shared with Alembic (``target_metadata``)."""


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(1024), index=True)
    canonical_url: Mapped[str | None] = mapped_column(String(1024), index=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    source_name: Mapped[str | None] = mapped_column(String(128))
    source_id: Mapped[str | None] = mapped_column(String(128), index=True)
    external_id: Mapped[str | None] = mapped_column(String(256), index=True)

    title: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_utc)

    http_status: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(128))
    content: Mapped[str | None] = mapped_column(Text)  # raw HTML / payload
    text: Mapped[str | None] = mapped_column(Text)  # normalized text
    content_hash: Mapped[str] = mapped_column(String(64), index=True)

    parser_version: Mapped[str | None] = mapped_column(String(32))
    parser_status: Mapped[str] = mapped_column(String(32), default="pending")
    parser_error: Mapped[str | None] = mapped_column(Text)
    adapter_version: Mapped[str | None] = mapped_column(String(32))
    relevant: Mapped[bool] = mapped_column(Boolean, default=False)

    facts: Mapped[list[ExtractedFact]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (UniqueConstraint("content_hash", "url", name="uq_doc_hash_url"),)


class ExtractedFact(Base):
    __tablename__ = "extracted_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )

    entity: Mapped[str] = mapped_column(String(64))
    field: Mapped[str] = mapped_column(String(64))
    value: Mapped[Any] = mapped_column(JSON)
    # Set for person names only: the normalized form of ``value``, so "the same
    # person named elsewhere" is an indexed lookup instead of a scan. ``value``
    # is JSON and cannot be matched on directly.
    normalized_value: Mapped[str | None] = mapped_column(String(512), index=True)
    verification_status: Mapped[str] = mapped_column(String(32), default="inferred")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    quote: Mapped[str | None] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_utc)

    document: Mapped[SourceDocument] = relationship(back_populates="facts")


# The name a person fact is searchable by is derived from the fact, not supplied
# by whoever builds it: a caller that constructs an ExtractedFact directly would
# otherwise leave the column empty and the row invisible to find_other_mentions,
# with nothing to signal it. Deriving it here makes the invariant hold for every
# path into the table.
@event.listens_for(ExtractedFact, "before_insert")
@event.listens_for(ExtractedFact, "before_update")
def _derive_normalized_value(_mapper: Any, _connection: Any, target: ExtractedFact) -> None:
    from court_monitor.normalization import normalize_fio  # noqa: PLC0415 - import cycle

    if target.field != PERSON_NAME_FIELD:
        target.normalized_value = None
        return
    target.normalized_value = normalize_fio(str(target.value)) or None


class PersonRecord(Base):
    """A record from an external registry (e.g. Rosfinmonitoring terrorist list).

    Stores raw, search, and normalized name separately — normalization is never
    assumed to be lossless. ``search_name`` is a lowercase fold for fast lookup;
    ``normalized_name`` is the best-effort canonical form.
    """

    __tablename__ = "person_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)  # "rfm", etc.

    raw_name: Mapped[str] = mapped_column(Text)
    search_name: Mapped[str] = mapped_column(String(512), index=True)
    normalized_name: Mapped[str] = mapped_column(String(512), index=True)
    normalization_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    normalization_method: Mapped[str] = mapped_column(String(64), default="lowercase")

    birth_date: Mapped[str | None] = mapped_column(String(32), index=True)
    birth_place: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(128))
    source_ref: Mapped[str | None] = mapped_column(String(128))
    added_date: Mapped[str | None] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    raw_line: Mapped[str | None] = mapped_column(Text)
    gender: Mapped[str | None] = mapped_column(String(16))
    country: Mapped[str | None] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(256))
    extra_json: Mapped[str | None] = mapped_column(Text)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_utc)

    __table_args__ = (
        UniqueConstraint(
            "source",
            "normalized_name",
            "birth_date",
            "birth_place",
            name="uq_person_source_name_birth_place",
        ),
    )


class MatchCandidate(Base):
    """A potential match between an extracted person fact and a PersonRecord.

    Never auto-confirmed — always requires operator review.
    """

    __tablename__ = "person_match_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    extracted_fact_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_facts.id", ondelete="CASCADE"), index=True
    )
    person_record_id: Mapped[int] = mapped_column(
        ForeignKey("person_records.id", ondelete="CASCADE"), index=True
    )

    score: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)

    name_score: Mapped[float] = mapped_column(Float, default=0.0)
    birth_date_score: Mapped[float] = mapped_column(Float, default=0.0)
    birthplace_score: Mapped[float] = mapped_column(Float, default=0.0)

    reasons_json: Mapped[str | None] = mapped_column(Text)
    conflicts_json: Mapped[str | None] = mapped_column(Text)
    algorithm_version: Mapped[str] = mapped_column(String(32))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_utc)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_comment: Mapped[str | None] = mapped_column(Text)

    extracted_fact: Mapped[ExtractedFact] = relationship()
    person_record: Mapped[PersonRecord] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "extracted_fact_id",
            "person_record_id",
            name="uq_fact_person_record",
        ),
    )


class ReviewItem(Base):
    """Something an operator needs to look at.

    Generic on purpose: distinct from ``MatchCandidate`` (person-match-only),
    this covers everything else that currently only reaches a log line —
    parser failures now, source blocks / structural drift later (spec §15,
    §24.2). ``item_type`` is intentionally a free-form string, not an enum:
    there is exactly one producer today (``parser_failed``); a closed enum
    would be premature until a second one exists.
    """

    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_type: Mapped[str] = mapped_column(String(64), index=True)
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL"), index=True
    )
    source_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    data_json: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_utc)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(128))
    resolution_comment: Mapped[str | None] = mapped_column(Text)

    document: Mapped[SourceDocument | None] = relationship()


class AuditLog(Base):
    """Append-only record of operator decisions (spec §17, discovery.md §7).

    Rows are never updated or deleted by application code — only inserted.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64), index=True)
    object_type: Mapped[str] = mapped_column(String(64), index=True)
    object_id: Mapped[int] = mapped_column(Integer, index=True)
    old_value_json: Mapped[str | None] = mapped_column(Text)
    new_value_json: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_utc)


class Job(Base):
    """A long-running pipeline operation started from the web UI.

    Fetching every source takes minutes, far longer than a request may hold, so
    the work runs in the background and its state lives here — that is also what
    lets the UI show progress and, more importantly, what a failure *was*
    instead of losing it to a log line nobody reads.

    ``kind`` is a free-form string for the same reason ``ReviewItem.item_type``
    is: the set of operations is still moving.
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    actor: Mapped[str] = mapped_column(String(128))

    params_json: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_active(self) -> bool:
        return self.status in {"queued", "running"}

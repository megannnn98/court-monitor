"""SQLAlchemy 2 ORM models.

* ``SourceDocument`` — fetched page/file with provenance + dedup hash.
* ``ExtractedFact``  — every extracted value with status/confidence/quote.
* ``PersonRecord``   — entries from external registries (e.g. Rosfinmonitoring).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
    relevant: Mapped[bool] = mapped_column(Integer, default=0)

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
    verification_status: Mapped[str] = mapped_column(String(32), default="inferred")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    quote: Mapped[str | None] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_utc)

    document: Mapped[SourceDocument] = relationship(back_populates="facts")


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

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_utc)

    __table_args__ = (
        UniqueConstraint(
            "source", "normalized_name", "birth_date",
            name="uq_person_source_name_birth",
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
            "extracted_fact_id", "person_record_id",
            name="uq_fact_person_record",
        ),
    )

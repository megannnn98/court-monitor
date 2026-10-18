"""The ingestion's tables: sources, the documents fetched from them, the articles parsed.

Internal: only this package reads and writes them; others go through its public
API (docs/architecture/data-ownership.md). Every class registers its table on
`db.models.base.Base`; `migrations/model_registry.py` composes them for Alembic.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    # The Airtable record this row was last matched to, once a sync has named it. NULL
    # for a source the operator never listed; the row stays, since the code registry
    # and the articles are what make a source exist.
    external_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    # Cleared by a sync when the source is no longer worth loading. Nothing deletes the
    # row or its articles.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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
        # "People in the news of a period" filters articles by publication date.
        Index("ix_parsed_articles_published_at", "published_at"),
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

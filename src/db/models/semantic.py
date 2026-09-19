"""Semantic documents, and their vectors when pgvector is the vector store (ADR 0018)."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from db.models.base import Base


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


class Vector(UserDefinedType[list[float]]):
    """pgvector's `vector` without a dimension: each collection records its own size."""

    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "vector"


class SemanticVectorCollectionRecord(Base):
    """A logical vector collection (`persons_semantic`, ...) and its vector size."""

    __tablename__ = "semantic_vector_collections"
    __table_args__ = (
        CheckConstraint("vector_size > 0", name="ck_semantic_vector_collections_size"),
    )

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    vector_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SemanticVectorRecord(Base):
    """One entity's vector in pgvector. Holds no facts: the same fields as a Qdrant point."""

    __tablename__ = "semantic_vectors"

    collection_name: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("semantic_vector_collections.name", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    entity_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
    embedding_model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    representation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SemanticIndexStateRecord(Base):
    """Which vector backend the `indexed_at` marks of an entity type belong to."""

    __tablename__ = "semantic_index_state"

    entity_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    vector_backend: Mapped[str] = mapped_column(String(32), nullable=False)
    rebuilt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

"""add semantic_documents (derived entity representations for retrieval)

Revision ID: m7n8o9p0q1r2
Revises: l6m7n8o9p0q1
Create Date: 2026-09-14 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m7n8o9p0q1r2"
down_revision: str | Sequence[str] | None = "l6m7n8o9p0q1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Derived, rebuildable text per canonical entity (ADR 0011).

    Not a source of truth: rows are rebuilt from persons/events. The text is
    what the embedder, the lexical entity retriever and the reranker see;
    `content_hash` + `representation_version` make incremental indexing possible
    and `indexed_at` records the last successful vector upsert.
    """
    op.create_table(
        "semantic_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("representation_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('russian'::regconfig, text)", persisted=True),
            nullable=False,
        ),
        sa.UniqueConstraint("entity_type", "entity_id", name="uq_semantic_documents_entity"),
    )
    op.create_index(
        "ix_semantic_documents_search_vector_gin",
        "semantic_documents",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_semantic_documents_search_vector_gin", table_name="semantic_documents")
    op.drop_table("semantic_documents")

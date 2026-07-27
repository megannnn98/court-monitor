"""initial schema: source_documents, extracted_facts

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("canonical_url", sa.String(length=1024), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_name", sa.String(length=128), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column("parser_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("parser_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("content_hash", "url", name="uq_doc_hash_url"),
    )
    op.create_index("ix_source_documents_url", "source_documents", ["url"])
    op.create_index("ix_source_documents_source_type", "source_documents", ["source_type"])
    op.create_index("ix_source_documents_content_hash", "source_documents", ["content_hash"])

    op.create_table(
        "extracted_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity", sa.String(length=64), nullable=False),
        sa.Column("field", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column(
            "verification_status", sa.String(length=32), nullable=False, server_default="inferred"
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("extraction_method", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_extracted_facts_document_id", "extracted_facts", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_extracted_facts_document_id", table_name="extracted_facts")
    op.drop_table("extracted_facts")
    op.drop_index("ix_source_documents_content_hash", table_name="source_documents")
    op.drop_index("ix_source_documents_source_type", table_name="source_documents")
    op.drop_index("ix_source_documents_url", table_name="source_documents")
    op.drop_table("source_documents")

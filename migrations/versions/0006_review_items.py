"""add review_items table

Revision ID: 0006_review_items
Revises: 0005_match_candidates
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_review_items"
down_revision: str | None = "0005_match_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_type", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_id", sa.String(length=128), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("data_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=128), nullable=True),
        sa.Column("resolution_comment", sa.Text(), nullable=True),
    )
    op.create_index("ix_review_items_item_type", "review_items", ["item_type"])
    op.create_index("ix_review_items_document_id", "review_items", ["document_id"])
    op.create_index("ix_review_items_source_id", "review_items", ["source_id"])
    op.create_index("ix_review_items_status", "review_items", ["status"])


def downgrade() -> None:
    op.drop_index("ix_review_items_status", table_name="review_items")
    op.drop_index("ix_review_items_source_id", table_name="review_items")
    op.drop_index("ix_review_items_document_id", table_name="review_items")
    op.drop_index("ix_review_items_item_type", table_name="review_items")
    op.drop_table("review_items")

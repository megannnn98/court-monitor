"""add source registry provenance + adapter/content-type columns

Revision ID: 0002_registry_provenance
Revises: 0001_initial
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002_registry_provenance"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_documents") as batch:
        batch.add_column(sa.Column("source_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("external_id", sa.String(length=256), nullable=True))
        batch.add_column(sa.Column("content_type", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("adapter_version", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column("relevant", sa.Integer(), nullable=False, server_default="0")
        )
        batch.alter_column("canonical_url", existing_type=sa.String(length=1024), nullable=True)
    op.create_index("ix_source_documents_canonical_url", "source_documents", ["canonical_url"])
    op.create_index("ix_source_documents_source_id", "source_documents", ["source_id"])
    op.create_index("ix_source_documents_external_id", "source_documents", ["external_id"])


def downgrade() -> None:
    op.drop_index("ix_source_documents_external_id", table_name="source_documents")
    op.drop_index("ix_source_documents_source_id", table_name="source_documents")
    op.drop_index("ix_source_documents_canonical_url", table_name="source_documents")
    with op.batch_alter_table("source_documents") as batch:
        batch.drop_column("relevant")
        batch.drop_column("adapter_version")
        batch.drop_column("content_type")
        batch.drop_column("external_id")
        batch.drop_column("source_id")

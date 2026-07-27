"""add search_name and normalization_method to person_records

Revision ID: 0004_person_names
Revises: 0003_person_records
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004_person_names"
down_revision: str | None = "0003_person_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("person_records") as batch:
        batch.add_column(sa.Column("search_name", sa.String(length=512), nullable=False, server_default=""))
        batch.add_column(sa.Column("normalization_method", sa.String(length=64), nullable=False, server_default="lowercase"))
    op.create_index("ix_person_records_search_name", "person_records", ["search_name"])


def downgrade() -> None:
    op.drop_index("ix_person_records_search_name", table_name="person_records")
    with op.batch_alter_table("person_records") as batch:
        batch.drop_column("normalization_method")
        batch.drop_column("search_name")

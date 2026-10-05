"""The operator's own table of the Rosfinmonitoring list; where an entry's day came from

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """A copy of the operator's table, replaced whole on every read; and a nullable mark
    on our entries — empty for every day already written, which came from ОВД-Инфо."""
    op.create_table(
        "rfm_operator_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.String(length=512), nullable=False),
        sa.Column("normalized_name", sa.String(length=512), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("birth_place", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("added_on", sa.Date(), nullable=True),
        sa.Column("removed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=255), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_rfm_operator_entries_normalized_name", "rfm_operator_entries", ["normalized_name"]
    )
    op.add_column(
        "rosfinmonitoring_entries",
        sa.Column("inclusion_source", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rosfinmonitoring_entries", "inclusion_source")
    op.drop_index("ix_rfm_operator_entries_normalized_name", table_name="rfm_operator_entries")
    op.drop_table("rfm_operator_entries")

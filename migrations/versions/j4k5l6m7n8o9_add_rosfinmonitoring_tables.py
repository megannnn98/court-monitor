"""Add Rosfinmonitoring tables

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-09-13 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "j4k5l6m7n8o9"
down_revision: Union[str, None] = "i3j4k5l6m7n8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rosfinmonitoring_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_rosfinmonitoring_snapshots_content_hash"),
    )
    op.create_index(
        "ix_rosfinmonitoring_snapshots_snapshot_date",
        "rosfinmonitoring_snapshots",
        ["snapshot_date"],
    )

    op.create_table(
        "rosfinmonitoring_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=512), nullable=False),
        sa.Column("normalized_name", sa.String(length=512), nullable=False),
        sa.Column("matching_key", sa.String(length=255), nullable=False),
        sa.Column("birth_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("birth_place", sa.String(length=512), nullable=True),
        sa.Column("snils", sa.String(length=20), nullable=True),
        sa.Column("inn", sa.String(length=20), nullable=True),
        sa.Column("inclusion_reason", sa.Text(), nullable=True),
        sa.Column("inclusion_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("raw_data", JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["rosfinmonitoring_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "full_name",
            "birth_date",
            name="uq_rosfinmonitoring_entries_snapshot_name_birth",
        ),
    )
    op.create_index(
        "ix_rosfinmonitoring_entries_snapshot_id",
        "rosfinmonitoring_entries",
        ["snapshot_id"],
    )
    op.create_index(
        "ix_rosfinmonitoring_entries_matching_key",
        "rosfinmonitoring_entries",
        ["matching_key"],
    )
    op.create_index(
        "ix_rosfinmonitoring_entries_normalized_name",
        "rosfinmonitoring_entries",
        ["normalized_name"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rosfinmonitoring_entries_normalized_name",
        table_name="rosfinmonitoring_entries",
    )
    op.drop_index(
        "ix_rosfinmonitoring_entries_matching_key",
        table_name="rosfinmonitoring_entries",
    )
    op.drop_index(
        "ix_rosfinmonitoring_entries_snapshot_id",
        table_name="rosfinmonitoring_entries",
    )
    op.drop_table("rosfinmonitoring_entries")
    op.drop_index(
        "ix_rosfinmonitoring_snapshots_snapshot_date",
        table_name="rosfinmonitoring_snapshots",
    )
    op.drop_table("rosfinmonitoring_snapshots")

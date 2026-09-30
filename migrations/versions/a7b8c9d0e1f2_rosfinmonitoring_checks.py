"""One run of the Rosfinmonitoring check against the entity list

Revision ID: a7b8c9d0e1f2
Revises: b1c2d3e4f5a6
Create Date: 2026-09-30 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Derived: one row per check run. What it answers is «was anybody checked at all» —
    without it «не найден в перечне» reads the same for a person nobody ever compared."""
    op.create_table(
        "rosfinmonitoring_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("rosfinmonitoring_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entities", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("listed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("maybe_listed", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_rosfinmonitoring_checks_checked_at", "rosfinmonitoring_checks", ["checked_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_rosfinmonitoring_checks_checked_at", table_name="rosfinmonitoring_checks")
    op.drop_table("rosfinmonitoring_checks")

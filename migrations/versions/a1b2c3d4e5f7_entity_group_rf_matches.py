"""Rosfinmonitoring list matches of the person entities

Revision ID: a1b2c3d4e5f7
Revises: z0a1b2c3d4e5
Create Date: 2026-09-25 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: str | Sequence[str] | None = "z0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Derived: every check rewrites it, an entity rebuild drops it with the groups."""
    op.create_table(
        "entity_group_rf_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("entity_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entry_id",
            sa.Integer(),
            sa.ForeignKey("rosfinmonitoring_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("level", sa.String(length=8), nullable=False),
    )
    op.create_index("ix_entity_group_rf_matches_group_id", "entity_group_rf_matches", ["group_id"])


def downgrade() -> None:
    op.drop_table("entity_group_rf_matches")

"""add_rosfin_matches_table

Revision ID: 75322e20112f
Revises: j4k5l6m7n8o9
Create Date: 2026-09-13 16:56:17.480842

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "75322e20112f"
down_revision: Union[str, Sequence[str], None] = "j4k5l6m7n8o9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create rosfin_matches table."""
    op.create_table(
        "rosfin_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("matched_entry_id", sa.Integer(), nullable=True),
        sa.Column("matched_entry_name", sa.String(512), nullable=True),
        sa.Column("candidate_entries", sa.JSON(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["person_id"], ["persons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["rosfinmonitoring_snapshots.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["matched_entry_id"], ["rosfinmonitoring_entries.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("person_id", "snapshot_id", name="uq_rosfin_matches_person_snapshot"),
    )
    op.create_index("ix_rosfin_matches_person_id", "rosfin_matches", ["person_id"])
    op.create_index("ix_rosfin_matches_snapshot_id", "rosfin_matches", ["snapshot_id"])
    op.create_index("ix_rosfin_matches_status", "rosfin_matches", ["status"])


def downgrade() -> None:
    """Drop rosfin_matches table."""
    op.drop_index("ix_rosfin_matches_status", table_name="rosfin_matches")
    op.drop_index("ix_rosfin_matches_snapshot_id", table_name="rosfin_matches")
    op.drop_index("ix_rosfin_matches_person_id", table_name="rosfin_matches")
    op.drop_table("rosfin_matches")

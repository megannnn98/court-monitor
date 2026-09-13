"""add unique index on persons matching_key for active persons

Revision ID: k5l6m7n8o9p0
Revises: 75322e20112f
Create Date: 2026-09-13 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k5l6m7n8o9p0"
down_revision: str | Sequence[str] | None = "75322e20112f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Prevent duplicate canonical persons for the same matching_key.

    Concurrent resolution of a brand-new name could otherwise race
    (find-then-create without a lock) and create two active Person rows
    with the same matching_key. Scoped to status='active' so a merged
    person keeps its old matching_key without blocking the constraint.
    """
    op.create_index(
        "uq_persons_matching_key_active",
        "persons",
        ["matching_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_persons_matching_key_active", table_name="persons")

"""A person's decisions on entities that may be one person

Revision ID: d4e5f6a7b8c0
Revises: c3d4e5f6a7b9
Create Date: 2026-09-25 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c0"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Not derived: a person's work, kept through every rebuild."""
    op.create_table(
        "entity_pair_decisions",
        sa.Column("key_a", sa.String(length=255), primary_key=True),
        sa.Column("key_b", sa.String(length=255), primary_key=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("entity_pair_decisions")

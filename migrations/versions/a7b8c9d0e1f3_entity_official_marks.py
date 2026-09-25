"""A person's marks of officials among the entities

Revision ID: a7b8c9d0e1f3
Revises: f6a7b8c9d0e2
Create Date: 2026-09-26 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f3"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Not derived: a person's work, kept through every rebuild."""
    op.create_table(
        "entity_official_marks",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("official", sa.Boolean(), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("entity_official_marks")

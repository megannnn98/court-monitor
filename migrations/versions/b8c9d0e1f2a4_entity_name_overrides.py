"""A person's corrections of entity names

Revision ID: b8c9d0e1f2a4
Revises: a7b8c9d0e1f3
Create Date: 2026-09-26 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a4"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Not derived: a person's work, kept through every rebuild."""
    op.create_table(
        "entity_name_overrides",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("entity_name_overrides")

"""A person's decisions on an entity's role and political classification

Revision ID: f1a2b3c4d5e6
Revises: 70ce9dc64a5a
Create Date: 2026-09-29 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "70ce9dc64a5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Not derived: a person's work, kept through every rebuild."""
    op.create_table(
        "entity_role_decisions",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "entity_politics_decisions",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("entity_politics_decisions")
    op.drop_table("entity_role_decisions")

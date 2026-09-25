"""Political or common crime: the figurants' cases, and the model's cached answers

Revision ID: f6a7b8c9d0e2
Revises: e5f6a7b8c9d1
Create Date: 2026-09-26 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e2"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entity_group_politics",
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("entity_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
    )
    op.create_table(
        "entity_politics_answers",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("input_hash", sa.String(length=64), primary_key=True),
        sa.Column("prompt_version", sa.String(length=32), primary_key=True),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("entity_politics_answers")
    op.drop_table("entity_group_politics")

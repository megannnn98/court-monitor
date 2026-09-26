"""What a political case's latest news is, and a model's readings of it

Revision ID: d0e1f2a4b5c6
Revises: c9d0e1f2a4b5
Create Date: 2026-09-26 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a4b5c6"
down_revision: str | Sequence[str] | None = "c9d0e1f2a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Derived: rewritten by every «Отобрать политические дела».
    op.create_table(
        "entity_group_news",
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("entity_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    # A cache: the model is not asked twice about the same quotes.
    op.create_table(
        "entity_news_answers",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("input_hash", sa.String(length=64), primary_key=True),
        sa.Column("prompt_version", sa.String(length=32), primary_key=True),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("entity_news_answers")
    op.drop_table("entity_group_news")

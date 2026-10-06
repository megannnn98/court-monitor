"""The unnamed the search set aside, to be seen; and those a person took back

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-10-06 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "h8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "g7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """`unnamed_screened` is derived, rewritten by every search beside
    `unnamed_figurants`; `unnamed_keeps` is a person's word and is never rewritten."""
    op.create_table(
        "unnamed_screened",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("parsed_articles.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("gender", sa.String(length=8), nullable=True),
        sa.Column("place", sa.Text(), nullable=False),
        sa.Column("initial", sa.String(length=4), nullable=True),
        sa.Column("articles", postgresql.JSONB(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "unnamed_keeps",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("unnamed_keeps")
    op.drop_table("unnamed_screened")

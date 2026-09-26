"""Unnamed figurants, a model's readings of them, and a person's decisions

Revision ID: c9d0e1f2a4b5
Revises: b8c9d0e1f2a4
Create Date: 2026-09-26 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9d0e1f2a4b5"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Derived: rewritten by every search.
    op.create_table(
        "unnamed_figurants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("parsed_articles.id", ondelete="CASCADE"),
            nullable=False,
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
    )
    op.create_index("ix_unnamed_figurants_article_id", "unnamed_figurants", ["article_id"])
    # A cache: the model is not asked twice about the same sentence.
    op.create_table(
        "unnamed_answers",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("input_hash", sa.String(length=64), primary_key=True),
        sa.Column("prompt_version", sa.String(length=32), primary_key=True),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    # Not derived: a person's work, kept through every search.
    op.create_table(
        "unnamed_decisions",
        sa.Column("figurant_key", sa.String(length=64), primary_key=True),
        sa.Column("candidate", sa.String(length=255), primary_key=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("unnamed_decisions")
    op.drop_table("unnamed_answers")
    op.drop_index("ix_unnamed_figurants_article_id", table_name="unnamed_figurants")
    op.drop_table("unnamed_figurants")

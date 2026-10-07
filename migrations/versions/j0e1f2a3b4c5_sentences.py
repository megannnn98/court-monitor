"""Court sentences read from the articles, and the questions asked about them

Revision ID: j0e1f2a3b4c5
Revises: i9d0e1f2a3b4
Create Date: 2026-10-07 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "j0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "i9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "article_sentence_readings",
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["article_id"], ["parsed_articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("article_id"),
    )
    op.create_table(
        "article_sentences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("person", sa.Text(), nullable=False),
        sa.Column("person_key", sa.String(length=255), nullable=True),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("fine_rub", sa.BigInteger(), nullable=False),
        sa.Column("in_absentia", sa.Boolean(), nullable=False),
        sa.Column("sentenced_on", sa.String(length=10), nullable=False),
        sa.Column("articles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("reason_text", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["parsed_articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_article_sentences_article_id", "article_sentences", ["article_id"])
    op.create_table(
        "chat_questions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "asked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("calls", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("chat_questions")
    op.drop_index("ix_article_sentences_article_id", table_name="article_sentences")
    op.drop_table("article_sentences")
    op.drop_table("article_sentence_readings")

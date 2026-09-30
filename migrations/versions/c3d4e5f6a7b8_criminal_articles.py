"""Справочник статей, которые нас интересуют

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-09-30 14:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """A list of numbers, kept beside the other reference lists.

    Not a column on `parsed_articles`: that table holds what the pipeline has already
    found, this one holds what to look for.
    """
    op.create_table(
        "criminal_articles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("article_text", sa.String(length=512), nullable=False),
        sa.Column("article_key", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_criminal_articles_article_key", "criminal_articles", ["article_key"])


def downgrade() -> None:
    op.drop_index("ix_criminal_articles_article_key", table_name="criminal_articles")
    op.drop_table("criminal_articles")

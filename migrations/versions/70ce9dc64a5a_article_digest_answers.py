"""A model's verdict on whether an article is substantively about a person's own case,
cached by article id

Revision ID: 70ce9dc64a5a
Revises: f2a4b5c6d7e8
Create Date: 2026-09-28 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "70ce9dc64a5a"
down_revision: str | Sequence[str] | None = "f2a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "article_digest_answers",
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("parsed_articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("relevant", sa.Boolean(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("article_digest_answers")

"""Articles the embedding screen held back from the junk purge

Revision ID: f2a4b5c6d7e8
Revises: e1f2a4b5c6d7
Create Date: 2026-09-27 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a4b5c6d7e8"
down_revision: str | Sequence[str] | None = "e1f2a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "junk_screen_holds",
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("parsed_articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="held"),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("cutoff", sa.Float(), nullable=False),
        sa.Column("screen", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("junk_screen_holds")

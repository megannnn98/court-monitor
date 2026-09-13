"""drop article_chunks, add parsed_articles search vector

Revision ID: a3f7c9d21b44
Revises: df2c42a78b48
Create Date: 2026-09-13 09:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a3f7c9d21b44"
down_revision: str | Sequence[str] | None = "df2c42a78b48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table("article_chunks")
    op.add_column(
        "parsed_articles",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('russian'::regconfig, text)", persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_parsed_articles_search_vector_gin",
        "parsed_articles",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_parsed_articles_search_vector_gin", table_name="parsed_articles", postgresql_using="gin"
    )
    op.drop_column("parsed_articles", "search_vector")
    op.create_table(
        "article_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parsed_article_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('russian'::regconfig, text)", persisted=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["parsed_article_id"],
            ["parsed_articles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "parsed_article_id", "ordinal", name="uq_article_chunks_parsed_article_id_ordinal"
        ),
    )
    op.create_index(
        "ix_article_chunks_search_vector_gin",
        "article_chunks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )

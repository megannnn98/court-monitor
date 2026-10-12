"""Windows of stored publications as vectors, for the investigation's dense retrieval

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-10-12 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "m3n4o5p6q7r8"
down_revision: str | Sequence[str] | None = "l2m3n4o5p6q7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class _Vector(sa.types.UserDefinedType[list[float]]):
    """pgvector's `vector`, without a dimension: one table for any embedding model."""

    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "vector"


def upgrade() -> None:
    # pgvector is installed by an earlier revision (t4u5v6w7x8y9); asked again so this
    # table never depends on that history.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "publication_vectors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("window_end", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", _Vector(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["article_id"], ["parsed_articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "article_id", "model_id", "window_start", name="uq_publication_vectors_window"
        ),
    )
    op.create_index("ix_publication_vectors_model_id", "publication_vectors", ["model_id"])
    # A 768-d vector is 3 KB: stored PLAIN an exact scan reads the heap, not TOAST (as
    # semantic_vectors, ADR 0018).
    op.execute("ALTER TABLE publication_vectors ALTER COLUMN embedding SET STORAGE PLAIN")


def downgrade() -> None:
    op.drop_index("ix_publication_vectors_model_id", table_name="publication_vectors")
    op.drop_table("publication_vectors")

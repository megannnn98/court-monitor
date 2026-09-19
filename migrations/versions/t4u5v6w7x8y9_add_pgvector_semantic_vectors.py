"""pgvector: semantic vectors in PostgreSQL next to Qdrant (ADR 0018)

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
Create Date: 2026-09-19 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t4u5v6w7x8y9"
down_revision: str | Sequence[str] | None = "s3t4u5v6w7x8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class _Vector(sa.types.UserDefinedType[list[float]]):
    """pgvector's `vector`, without a dimension."""

    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "vector"


def upgrade() -> None:
    """A second vector store for the same derived index; Qdrant stays the default.

    The embedding column has no fixed dimension: a collection records its size, and its
    HNSW index is a partial expression index `(embedding::vector(N))` created by the
    store, so another embedding model needs no migration.
    """
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "semantic_vector_collections",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("vector_size", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("vector_size > 0", name="ck_semantic_vector_collections_size"),
        sa.PrimaryKeyConstraint("name", name="pk_semantic_vector_collections"),
    )
    op.create_table(
        "semantic_vectors",
        sa.Column("collection_name", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("embedding", _Vector(), nullable=False),
        sa.Column("embedding_model_id", sa.String(length=200), nullable=False),
        sa.Column("representation_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["collection_name"],
            ["semantic_vector_collections.name"],
            name="fk_semantic_vectors_collection",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "collection_name", "entity_type", "entity_id", name="pk_semantic_vectors"
        ),
    )
    op.create_table(
        "semantic_index_state",
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("vector_backend", sa.String(length=32), nullable=False),
        sa.Column(
            "rebuilt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("entity_type", name="pk_semantic_index_state"),
    )
    # The `indexed_at` marks that exist today were set by Qdrant rebuilds: record that,
    # so an incremental run on Qdrant keeps working and one on pgvector asks for a full
    # rebuild instead of skipping every "already indexed" document.
    op.execute(
        "INSERT INTO semantic_index_state (entity_type, vector_backend) "
        "SELECT DISTINCT entity_type, 'qdrant' FROM semantic_documents "
        "WHERE indexed_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_table("semantic_index_state")
    op.drop_table("semantic_vectors")
    op.drop_table("semantic_vector_collections")
    # Drop `vector` only if nothing else uses it: it may have been there before this
    # migration, for another table.
    other_users = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid "
                "WHERE t.typname = 'vector' AND NOT a.attisdropped LIMIT 1"
            )
        )
        .first()
    )
    if other_users is None:
        op.execute("DROP EXTENSION IF EXISTS vector")

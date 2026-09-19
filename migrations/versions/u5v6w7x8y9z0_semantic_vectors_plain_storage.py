"""semantic_vectors.embedding stored PLAIN: exact PERSON search reads the heap (ADR 0018)

Revision ID: u5v6w7x8y9z0
Revises: t4u5v6w7x8y9
Create Date: 2026-09-19 14:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "u5v6w7x8y9z0"
down_revision: str | Sequence[str] | None = "t4u5v6w7x8y9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """A 768-d vector is 3 KB, above the 2 KB TOAST threshold: stored EXTERNAL (the
    `vector` type's default) it is detoasted row by row, and an exact scan of 15 k persons
    takes ~57 ms instead of ~15 ms.

    SET STORAGE only changes how rows written from now on are stored; it does not rewrite
    existing vectors. The full rebuild that switching to pgvector requires
    (IndexBackendMismatchError until then) writes every vector anew, PLAIN.
    """
    op.execute("ALTER TABLE semantic_vectors ALTER COLUMN embedding SET STORAGE PLAIN")


def downgrade() -> None:
    op.execute("ALTER TABLE semantic_vectors ALTER COLUMN embedding SET STORAGE EXTERNAL")

"""pgvector: semantic vectors in PostgreSQL next to Qdrant (ADR 0018) — emptied

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
Create Date: 2026-09-19 10:00:00.000000

This revision created the `vector` extension and the tables `semantic_vector_collections`,
`semantic_vectors` and `semantic_index_state`. The subsystem they belonged to was removed
on 2026-09-27 and the tables were left; ADR 0028 removes them and the extension.

The revision stays in the chain under its own id, so a database that ran it is still at
a known revision. It does nothing now: a new database must not need pgvector installed
to be migrated. A database that ran it while it still created the tables is cleaned by
`m4n5o6p7q8r9`.
"""

from collections.abc import Sequence

revision: str = "t4u5v6w7x8y9"
down_revision: str | Sequence[str] | None = "s3t4u5v6w7x8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing: see the module's note."""


def downgrade() -> None:
    """Nothing: what this revision once created is dropped by `m4n5o6p7q8r9`."""

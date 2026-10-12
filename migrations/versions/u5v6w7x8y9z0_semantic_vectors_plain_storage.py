"""semantic_vectors.embedding stored PLAIN (ADR 0018) — emptied

Revision ID: u5v6w7x8y9z0
Revises: t4u5v6w7x8y9
Create Date: 2026-09-19 14:00:00.000000

This revision changed the storage of `semantic_vectors.embedding`. The table is gone
(ADR 0028; see `t4u5v6w7x8y9`), and a new database never has it: the revision stays in
the chain under its own id and does nothing.
"""

from collections.abc import Sequence

revision: str = "u5v6w7x8y9z0"
down_revision: str | Sequence[str] | None = "t4u5v6w7x8y9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing: see the module's note."""


def downgrade() -> None:
    """Nothing: see the module's note."""

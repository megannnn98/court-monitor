"""Windows of stored publications as vectors, for the investigation's dense retrieval — emptied

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-10-12 15:00:00.000000

This revision created the table `publication_vectors` (and asked for the `vector`
extension). The dense retrieval was removed before it reached a working database
(ADR 0028). The revision stays in the chain under its own id and does nothing: it was
the head of `main` for a day, and a database migrated then — a developer's, a copy — is
at this revision and must still find it. Such a database is cleaned by `m4n5o6p7q8r9`.
"""

from collections.abc import Sequence

revision: str = "m3n4o5p6q7r8"
down_revision: str | Sequence[str] | None = "l2m3n4o5p6q7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing: see the module's note."""


def downgrade() -> None:
    """Nothing: what this revision once created is dropped by `m4n5o6p7q8r9`."""

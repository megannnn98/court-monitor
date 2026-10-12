"""Drop the tables of the removed vector retrieval and the pgvector extension

Revision ID: m4n5o6p7q8r9
Revises: m3n4o5p6q7r8
Create Date: 2026-10-13 10:00:00.000000

`semantic_documents`, `semantic_vector_collections`, `semantic_vectors` and
`semantic_index_state` held the derived index of the semantic retrieval (ADR 0011, 0018);
`publication_vectors` held the windows of the investigation's dense retrieval (ADR 0027).
Nothing reads or writes any of them (ADR 0028).

A database migrated before this revision may have them; a new one does not, because the
revisions that created the vector tables are empty now. Both end with none of these
tables.

The extension is another matter: it may not be this database's alone. It is dropped when
this role installed it and nothing is left that depends on it, and kept — with a line in
the migration's log saying why — in every other case. The migration never fails over it
and never drops anything of somebody else's.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.exc import DBAPIError

revision: str = "m4n5o6p7q8r9"
down_revision: str | Sequence[str] | None = "m3n4o5p6q7r8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# Children first: `semantic_vectors` refers to its collection.
_TABLES = (
    "publication_vectors",
    "semantic_index_state",
    "semantic_vectors",
    "semantic_vector_collections",
    "semantic_documents",
)
_OWNER = sa.text(
    "SELECT pg_get_userbyid(extowner) = current_user FROM pg_extension WHERE extname = 'vector'"
)
# PostgreSQL's `dependent_objects_still_exist`.
_STILL_USED = "2BP01"


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    bind = op.get_bind()
    ours = bind.execute(_OWNER).scalar()
    if ours is None:
        return
    if not ours:
        logger.warning(
            "The vector extension is kept: another role installed it, and it is theirs to drop."
        )
        return
    # Without CASCADE, inside a savepoint: whatever still depends on the extension — a
    # column, a domain over its type, a function that takes it, in any schema — makes the
    # database refuse, and the refusal is the check. Nothing of anybody's is dropped.
    try:
        with bind.begin_nested():
            bind.execute(sa.text("DROP EXTENSION vector"))
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != _STILL_USED:
            raise
        logger.warning(
            "The vector extension is kept: something outside the dropped tables depends on it."
        )


def downgrade() -> None:
    """Nothing is brought back: the tables held indexes of code that no longer exists,
    and the extension is not a dependency of anything."""

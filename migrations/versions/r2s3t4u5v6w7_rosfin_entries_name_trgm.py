"""rosfinmonitoring_entries.normalized_name gets a trigram index

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
Create Date: 2026-09-17 16:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "r2s3t4u5v6w7"
down_revision: str | Sequence[str] | None = "q1r2s3t4u5v6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The matcher fetches entries by `ILIKE '%stem%'`, which a btree cannot serve.

    Without this index every person scanned the whole list: 46 ms per query on the
    22 844 entries of a snapshot, 2.7 ms with it.
    """
    # pg_trgm is a trusted extension (PostgreSQL 13+): the database owner may create it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_rosfinmonitoring_entries_normalized_name_trgm",
        "rosfinmonitoring_entries",
        ["normalized_name"],
        postgresql_using="gin",
        postgresql_ops={"normalized_name": "gin_trgm_ops"},
    )


def downgrade() -> None:
    # pg_trgm is left installed: other objects depend on it.
    op.drop_index(
        "ix_rosfinmonitoring_entries_normalized_name_trgm",
        table_name="rosfinmonitoring_entries",
    )

"""rosfin_matches records the matcher that produced the result

Revision ID: q1r2s3t4u5v6
Revises: p0q1r2s3t4u5
Create Date: 2026-09-15 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q1r2s3t4u5v6"
down_revision: str | Sequence[str] | None = "p0q1r2s3t4u5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """A matcher rule change must recompute persisted results.

    Existing rows keep NULL: their matcher version is unknown, so monitoring
    treats them as produced by an older matcher and matches those persons again.
    """
    op.add_column("rosfin_matches", sa.Column("matcher_name", sa.String(255), nullable=True))
    op.add_column("rosfin_matches", sa.Column("matcher_version", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("rosfin_matches", "matcher_version")
    op.drop_column("rosfin_matches", "matcher_name")

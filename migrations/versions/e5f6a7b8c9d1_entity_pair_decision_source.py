"""Who decided a pair of entities: a person, or the Rosfinmonitoring check

Revision ID: e5f6a7b8c9d1
Revises: d4e5f6a7b8c0
Create Date: 2026-09-25 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d1"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "entity_pair_decisions",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
    )


def downgrade() -> None:
    op.drop_column("entity_pair_decisions", "source")

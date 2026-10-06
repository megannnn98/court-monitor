"""What a model read of an article the junk screen held

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
Create Date: 2026-10-06 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "h8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nullable: a hold nobody read yet has none of the three."""
    op.add_column("junk_screen_holds", sa.Column("reader", sa.String(length=100), nullable=True))
    op.add_column(
        "junk_screen_holds", sa.Column("reader_verdict", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "junk_screen_holds", sa.Column("reader_event", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("junk_screen_holds", "reader_event")
    op.drop_column("junk_screen_holds", "reader_verdict")
    op.drop_column("junk_screen_holds", "reader")

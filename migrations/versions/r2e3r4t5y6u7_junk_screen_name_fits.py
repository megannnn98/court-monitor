"""`junk_screen_holds.screen` fits the name of the JEV-1 + JEV-2 cascade

Revision ID: r2e3r4t5y6u7
Revises: q1w2e3r4t5y6
Create Date: 2026-10-21 17:30:00.000000

The cascade's name is `jev-screen-v1:<model>+jev-drop-verifier-v1:<prompt>:<model>:p_drop>=0.50`,
107 characters; the column held 100, so the first hold it wrote failed the purge step.
Text has no limit, so a longer model or prompt name cannot fail it again; varchar to text
changes no row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r2e3r4t5y6u7"
down_revision: str | Sequence[str] | None = "q1w2e3r4t5y6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "junk_screen_holds",
        "screen",
        existing_type=sa.String(length=100),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Fails, and rolls back, once a hold with a name over 100 characters exists: the names
    # are never cut. To roll the app back, leave the column wide.
    op.alter_column(
        "junk_screen_holds",
        "screen",
        existing_type=sa.Text(),
        type_=sa.String(length=100),
        existing_nullable=False,
    )

"""Model-normalized entity names

Revision ID: y9z0a1b2c3d4
Revises: x8y9z0a1b2c3
Create Date: 2026-09-24 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "y9z0a1b2c3d4"
down_revision: str | Sequence[str] | None = "x8y9z0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The model's answers are kept, so a rebuild asks only about new entities."""
    op.add_column("entity_groups", sa.Column("gender", sa.String(length=8), nullable=True))
    op.add_column(
        "entity_groups",
        sa.Column("name_source", sa.String(length=8), nullable=False, server_default="rules"),
    )
    op.create_table(
        "entity_name_normalizations",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("prompt_version", sa.String(length=32), primary_key=True),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("nominative", sa.Text(), nullable=False),
        sa.Column("gender", sa.String(length=8), nullable=False),
        sa.Column("is_person", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("entity_name_normalizations")
    op.drop_column("entity_groups", "name_source")
    op.drop_column("entity_groups", "gender")

"""Reference lists an operator keeps in Airtable

Revision ID: b1c2d3e4f5a6
Revises: f1a2b3c4d5e6
Create Date: 2026-09-29 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Not derived: a person's own lists, synced from Airtable and kept whole."""
    op.add_column("sources", sa.Column("external_id", sa.String(length=64), nullable=True))
    op.add_column(
        "sources", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.create_unique_constraint("uq_sources_external_id", "sources", ["external_id"])
    op.create_table(
        "airtable_known_persons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("full_name", sa.String(length=512), nullable=False),
        sa.Column("normalized_name", sa.String(length=512), nullable=False),
        sa.Column("matching_key", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_airtable_known_persons_matching_key", "airtable_known_persons", ["matching_key"]
    )
    op.create_table(
        "excluded_persons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("full_name", sa.String(length=512), nullable=False),
        sa.Column("normalized_name", sa.String(length=512), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="other"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_excluded_persons_normalized_name", "excluded_persons", ["normalized_name"])


def downgrade() -> None:
    op.drop_index("ix_excluded_persons_normalized_name", table_name="excluded_persons")
    op.drop_table("excluded_persons")
    op.drop_index("ix_airtable_known_persons_matching_key", table_name="airtable_known_persons")
    op.drop_table("airtable_known_persons")
    op.drop_constraint("uq_sources_external_id", "sources", type_="unique")
    op.drop_column("sources", "active")
    op.drop_column("sources", "external_id")

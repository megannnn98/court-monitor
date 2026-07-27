"""add person_records table for Rosfinmonitoring registry

Revision ID: 0003_person_records
Revises: 0002_registry_provenance
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_person_records"
down_revision: str | None = "0002_registry_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "person_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("raw_name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.String(length=512), nullable=False),
        sa.Column("normalization_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("birth_date", sa.String(length=32), nullable=True),
        sa.Column("birth_place", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("source_ref", sa.String(length=128), nullable=True),
        sa.Column("added_date", sa.String(length=32), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("raw_line", sa.Text(), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "source",
            "normalized_name",
            "birth_date",
            name="uq_person_source_name_birth",
        ),
    )
    op.create_index("ix_person_records_source", "person_records", ["source"])
    op.create_index("ix_person_records_normalized_name", "person_records", ["normalized_name"])
    op.create_index("ix_person_records_birth_date", "person_records", ["birth_date"])


def downgrade() -> None:
    op.drop_index("ix_person_records_birth_date", table_name="person_records")
    op.drop_index("ix_person_records_normalized_name", table_name="person_records")
    op.drop_index("ix_person_records_source", table_name="person_records")
    op.drop_table("person_records")

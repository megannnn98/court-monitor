"""add gender, country, region, extra_json to person_records

Revision ID: 0008_person_records_rfm_v2
Revises: 0007_audit_log
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_person_records_rfm_v2"
down_revision: str | None = "0007_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("person_records", sa.Column("gender", sa.String(length=16), nullable=True))
    op.add_column("person_records", sa.Column("country", sa.String(length=128), nullable=True))
    op.add_column("person_records", sa.Column("region", sa.String(length=256), nullable=True))
    op.add_column("person_records", sa.Column("extra_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("person_records", "extra_json")
    op.drop_column("person_records", "region")
    op.drop_column("person_records", "country")
    op.drop_column("person_records", "gender")

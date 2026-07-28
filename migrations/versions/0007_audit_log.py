"""add audit_log table

Revision ID: 0007_audit_log
Revises: 0006_review_items
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_audit_log"
down_revision: str | None = "0006_review_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.Column("old_value_json", sa.Text(), nullable=True),
        sa.Column("new_value_json", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_object_type", "audit_log", ["object_type"])
    op.create_index("ix_audit_log_object_id", "audit_log", ["object_id"])
    op.create_index("ix_audit_log_correlation_id", "audit_log", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_correlation_id", table_name="audit_log")
    op.drop_index("ix_audit_log_object_id", table_name="audit_log")
    op.drop_index("ix_audit_log_object_type", table_name="audit_log")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_table("audit_log")

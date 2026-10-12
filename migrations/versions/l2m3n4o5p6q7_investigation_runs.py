"""Stored investigation runs, to read a result again and resume a run

Revision ID: l2m3n4o5p6q7
Revises: k1f2a3b4c5d6
Create Date: 2026-10-12 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "l2m3n4o5p6q7"
down_revision: str | Sequence[str] | None = "k1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "investigation_runs",
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scenario", sa.String(length=32), nullable=True),
        sa.Column("round", sa.Integer(), server_default="0", nullable=False),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("failure", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_investigation_runs_created_at", "investigation_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_investigation_runs_created_at", table_name="investigation_runs")
    op.drop_table("investigation_runs")

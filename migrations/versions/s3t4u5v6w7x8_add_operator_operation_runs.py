"""operator_operation_runs: operation runs of the operator console, in PostgreSQL

Revision ID: s3t4u5v6w7x8
Revises: r2s3t4u5v6w7
Create Date: 2026-09-18 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "s3t4u5v6w7x8"
down_revision: str | Sequence[str] | None = "r2s3t4u5v6w7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Runs used to live in one API process's memory: a restart lost them, and two
    workers each allowed their own run of the same operation."""
    op.create_table(
        "operator_operation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_name", sa.String(length=64), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("command", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("return_code", sa.Integer(), nullable=True),
        sa.Column("stdout", sa.Text(), server_default="", nullable=False),
        sa.Column("stderr", sa.Text(), server_default="", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'interrupted')",
            name="ck_operator_operation_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_operator_operation_runs_created_at", "operator_operation_runs", ["created_at"]
    )
    # At most one live run of an operation, whichever API process started it.
    op.create_index(
        "uq_operator_operation_runs_active_operation",
        "operator_operation_runs",
        ["operation_name"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_operator_operation_runs_active_operation", table_name="operator_operation_runs"
    )
    op.drop_index("ix_operator_operation_runs_created_at", table_name="operator_operation_runs")
    op.drop_table("operator_operation_runs")

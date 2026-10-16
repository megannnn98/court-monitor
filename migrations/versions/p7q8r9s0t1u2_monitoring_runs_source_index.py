"""An index for a source's latest runs (the layout warning reads them on every page)

Revision ID: p7q8r9s0t1u2
Revises: o6p7q8r9s0t1
Create Date: 2026-10-16 15:30:00.000000

`orchestrator.source_health` asks, at every `/api/v1/status`, for each source's latest
runs; `ix_monitoring_runs_started_at` alone made it read the whole window of every source.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p7q8r9s0t1u2"
down_revision: str | Sequence[str] | None = "o6p7q8r9s0t1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_monitoring_runs_source_started_at",
        "monitoring_runs",
        ["source", sa.text("started_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("source IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_monitoring_runs_source_started_at", table_name="monitoring_runs")

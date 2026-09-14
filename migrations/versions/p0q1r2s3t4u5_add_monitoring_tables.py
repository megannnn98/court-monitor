"""add automated monitoring tables

Revision ID: p0q1r2s3t4u5
Revises: o9p0q1r2s3t4
Create Date: 2026-09-14 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p0q1r2s3t4u5"
down_revision: str | Sequence[str] | None = "o9p0q1r2s3t4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COUNTERS = (
    "documents_discovered",
    "documents_ingested",
    "documents_skipped",
    "documents_failed",
    "articles_extracted",
    "events_created",
    "persons_created",
    "persons_linked",
    "person_reviews_created",
    "classifications_created",
    "rf_matches_created",
    "semantic_entities_indexed",
    "findings_created",
    "error_count",
)


def upgrade() -> None:
    """ADR 0013: orchestration state only; existing domain rows are not touched."""
    op.create_table(
        "monitoring_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("trigger_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *(
            sa.Column(name, sa.Integer(), nullable=False, server_default=sa.text("0"))
            for name in _COUNTERS
        ),
        sa.Column(
            "rf_snapshot_id",
            sa.Integer(),
            sa.ForeignKey("rosfinmonitoring_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "stage_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_monitoring_runs_started_at", "monitoring_runs", ["started_at"])
    op.create_index("ix_monitoring_runs_status", "monitoring_runs", ["status"])
    op.create_index(
        "uq_monitoring_runs_running_scope",
        "monitoring_runs",
        ["scope"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )

    op.create_table(
        "monitoring_run_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("monitoring_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("external_ref", sa.String(length=2048), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("failure_kind", sa.String(length=32), nullable=False),
        sa.Column("error_type", sa.String(length=255), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_monitoring_run_items_run_id", "monitoring_run_items", ["run_id"])

    op.create_table(
        "source_monitoring_state",
        sa.Column("source_name", sa.String(length=64), primary_key=True),
        sa.Column(
            "last_successful_run_id",
            sa.Integer(),
            sa.ForeignKey("monitoring_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_successful_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_discovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_discovered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_external_marker", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "monitoring_findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("finding_type", sa.String(length=64), nullable=False),
        sa.Column(
            "person_id",
            sa.Integer(),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("criteria_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "first_seen_run_id",
            sa.Integer(),
            sa.ForeignKey("monitoring_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_run_id",
            sa.Integer(),
            sa.ForeignKey("monitoring_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("inactive_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("rosfinmonitoring_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "persecution_classification_id",
            sa.Integer(),
            sa.ForeignKey("persecution_classifications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "rosfin_match_id",
            sa.Integer(),
            sa.ForeignKey("rosfin_matches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "finding_type",
            "person_id",
            "criteria_version",
            name="uq_monitoring_findings_type_person_criteria",
        ),
    )
    op.create_index(
        "ix_monitoring_findings_first_seen_run", "monitoring_findings", ["first_seen_run_id"]
    )
    op.create_index("ix_monitoring_findings_active", "monitoring_findings", ["active"])


def downgrade() -> None:
    op.drop_table("monitoring_findings")
    op.drop_table("source_monitoring_state")
    op.drop_table("monitoring_run_items")
    op.drop_table("monitoring_runs")

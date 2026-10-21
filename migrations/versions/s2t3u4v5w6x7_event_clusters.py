"""Clusters of publications about one event, and the status of the case links

Revision ID: s2t3u4v5w6x7
Revises: r2e3d4f5a6b7
Create Date: 2026-10-21 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "s2t3u4v5w6x7"
down_revision: str | Sequence[str] | None = "r2e3d4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_clusters",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("core_evidence_key", sa.String(length=64), nullable=False),
        sa.Column(
            "core_article_id",
            sa.Integer(),
            sa.ForeignKey("parsed_articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("targets", postgresql.JSONB(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("candidates", postgresql.JSONB(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_event_clusters_core_evidence_key", "event_clusters", ["core_evidence_key"])
    op.create_index("ix_event_clusters_status", "event_clusters", ["status"])
    op.create_index(
        "ix_event_clusters_targets", "event_clusters", ["targets"], postgresql_using="gin"
    )
    op.create_table(
        "event_cluster_members",
        sa.Column("evidence_key", sa.String(length=64), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("extracted_events.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("parsed_articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cluster_id", sa.Uuid(), sa.ForeignKey("event_clusters.id", ondelete="CASCADE")),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("candidates", postgresql.JSONB(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
    )
    op.create_index("ix_event_cluster_members_cluster_id", "event_cluster_members", ["cluster_id"])
    op.create_index("ix_event_cluster_members_status", "event_cluster_members", ["status"])
    op.create_index("ix_event_cluster_members_article_id", "event_cluster_members", ["article_id"])
    # The status of a link: candidate | confirmed | rejected. The links made before this
    # migration were automatic and confirmed.
    op.add_column(
        "case_event_links",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="confirmed"),
    )
    # An event that names nobody forms no case: its link keeps the fact that it was
    # looked at, with no case (ADR 0033).
    op.alter_column("case_event_links", "case_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column("case_event_links", sa.Column("decided_by", sa.String(length=128), nullable=True))
    op.add_column(
        "case_event_links", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("case_event_links", sa.Column("decision_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM case_event_links WHERE case_id IS NULL")
    op.alter_column("case_event_links", "case_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("case_event_links", "decision_note")
    op.drop_column("case_event_links", "decided_at")
    op.drop_column("case_event_links", "decided_by")
    op.drop_column("case_event_links", "status")
    op.drop_index("ix_event_cluster_members_status", "event_cluster_members")
    op.drop_index("ix_event_cluster_members_cluster_id", "event_cluster_members")
    op.drop_table("event_cluster_members")
    op.drop_index("ix_event_clusters_status", "event_clusters")
    op.drop_index("ix_event_clusters_core_evidence_key", "event_clusters")
    op.drop_table("event_clusters")

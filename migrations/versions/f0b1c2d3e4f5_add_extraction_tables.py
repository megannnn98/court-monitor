"""add extraction tables

Revision ID: f0b1c2d3e4f5
Revises: a3f7c9d21b44
Create Date: 2026-09-13 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f0b1c2d3e4f5"
down_revision: str | Sequence[str] | None = "a3f7c9d21b44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "article_extraction_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("article_content_hash", sa.String(length=64), nullable=False),
        sa.Column("extractor_name", sa.String(length=255), nullable=False),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("normalizer_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["article_id"], ["parsed_articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "article_id",
            "article_content_hash",
            "extractor_name",
            "extractor_version",
            "normalizer_version",
            name="uq_article_extraction_runs_version",
        ),
    )
    op.create_table(
        "entity_mentions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("extraction_run_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("surface_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("normalized_data", postgresql.JSONB(), nullable=False),
        sa.Column("extractor_name", sa.String(length=255), nullable=False),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("normalizer_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["extraction_run_id"],
            ["article_extraction_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "extraction_run_id",
            "entity_type",
            "start_offset",
            "end_offset",
            name="uq_entity_mentions_run_type_span",
        ),
    )
    op.create_table(
        "extracted_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("extraction_run_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), nullable=False),
        sa.Column("extractor_name", sa.String(length=255), nullable=False),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["extraction_run_id"],
            ["article_extraction_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "event_entity_mentions",
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("mention_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["extracted_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mention_id"], ["entity_mentions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id", "mention_id", "role"),
        sa.UniqueConstraint(
            "event_id",
            "mention_id",
            "role",
            name="uq_event_entity_mentions_event_mention_role",
        ),
    )


def downgrade() -> None:
    op.drop_table("event_entity_mentions")
    op.drop_table("extracted_events")
    op.drop_table("entity_mentions")
    op.drop_table("article_extraction_runs")

"""Criminal cases and the evidence tying extracted events to them

Revision ID: r2e3d4f5a6b7
Revises: q1w2e3r4t5y6
Create Date: 2026-10-20 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "r2e3d4f5a6b7"
down_revision: str | Sequence[str] | None = "r2e3r4t5y6u7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "criminal_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("preliminary", sa.Boolean(), nullable=False),
        sa.Column("case_number", sa.String(length=128), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "case_event_links",
        sa.Column("evidence_key", sa.String(length=64), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("criminal_cases.id"), nullable=False),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("extracted_events.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("parsed_articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("case_number", sa.String(length=128), nullable=True),
        sa.Column("targets", postgresql.JSONB(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("candidates", postgresql.JSONB(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False),
        sa.Column(
            "resolved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_case_event_links_case_id", "case_event_links", ["case_id"])
    op.create_index("ix_case_event_links_article_id", "case_event_links", ["article_id"])
    op.create_index("ix_case_event_links_case_number", "case_event_links", ["case_number"])
    op.create_index(
        "ix_case_event_links_targets", "case_event_links", ["targets"], postgresql_using="gin"
    )
    op.create_table(
        "case_participants",
        sa.Column(
            "evidence_key",
            sa.String(length=64),
            sa.ForeignKey("case_event_links.evidence_key", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("person_key", sa.String(length=255), primary_key=True),
        sa.Column("role", sa.String(length=32), nullable=True),
    )
    op.create_index("ix_case_participants_person_key", "case_participants", ["person_key"])


def downgrade() -> None:
    op.drop_table("case_participants")
    op.drop_table("case_event_links")
    op.drop_table("criminal_cases")

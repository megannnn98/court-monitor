"""add person_match_candidates table

Revision ID: 0005_match_candidates
Revises: 0004_person_names
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005_match_candidates"
down_revision: str | None = "0004_person_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "person_match_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("extracted_fact_id", sa.Integer(), sa.ForeignKey("extracted_facts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_record_id", sa.Integer(), sa.ForeignKey("person_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("name_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("birth_date_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("birthplace_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reasons_json", sa.Text(), nullable=True),
        sa.Column("conflicts_json", sa.Text(), nullable=True),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.UniqueConstraint("extracted_fact_id", "person_record_id", name="uq_fact_person_record"),
    )
    op.create_index("ix_match_candidates_status", "person_match_candidates", ["status"])
    op.create_index("ix_match_candidates_fact", "person_match_candidates", ["extracted_fact_id"])
    op.create_index("ix_match_candidates_record", "person_match_candidates", ["person_record_id"])


def downgrade() -> None:
    op.drop_index("ix_match_candidates_record", table_name="person_match_candidates")
    op.drop_index("ix_match_candidates_fact", table_name="person_match_candidates")
    op.drop_index("ix_match_candidates_status", table_name="person_match_candidates")
    op.drop_table("person_match_candidates")

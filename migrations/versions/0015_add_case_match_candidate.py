"""Add CaseMatchCandidate table.

Revision ID: 0015
Revises: 0014_add_case_personcase_courtevent
Create Date: 2026-08-12

Adds table for storing case match candidates - matches between press releases
and court cases that need operator review.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_add_case_match_candidate"
down_revision: str | None = "0014_add_case_personcase_courtevent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_match_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("signals_json", sa.Text(), nullable=True),
        sa.Column("missing_json", sa.Text(), nullable=True),
        sa.Column("conflicts_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_document_id", "case_id", name="uq_case_match_candidate"),
    )
    op.create_index(
        "ix_case_match_candidates_source_document_id",
        "case_match_candidates",
        ["source_document_id"],
        unique=False,
    )
    op.create_index(
        "ix_case_match_candidates_case_id",
        "case_match_candidates",
        ["case_id"],
        unique=False,
    )
    op.create_index(
        "ix_case_match_candidates_status",
        "case_match_candidates",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_case_match_candidates_status", table_name="case_match_candidates")
    op.drop_index("ix_case_match_candidates_case_id", table_name="case_match_candidates")
    op.drop_index("ix_case_match_candidates_source_document_id", table_name="case_match_candidates")
    op.drop_table("case_match_candidates")

"""Add CaseParticipant table.

Revision ID: 0016
Revises: 0015_add_case_match_candidate
Create Date: 2026-08-12

Adds table for storing case participants from case cards,
independent of PersonRecord (RFM registry).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_add_case_participant"
down_revision: str | None = "0015_add_case_match_candidate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_participants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("name_original", sa.String(length=512), nullable=False),
        sa.Column("normalized_name", sa.String(length=512), nullable=True),
        sa.Column("articles", sa.Text(), nullable=True),
        sa.Column("material", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "name_original", name="uq_case_participant_name"),
    )
    op.create_index("ix_case_participants_case_id", "case_participants", ["case_id"])
    op.create_index(
        "ix_case_participants_normalized_name", "case_participants", ["normalized_name"]
    )
    op.create_index(
        "ix_case_participants_source_document_id", "case_participants", ["source_document_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_case_participants_source_document_id", table_name="case_participants")
    op.drop_index("ix_case_participants_normalized_name", table_name="case_participants")
    op.drop_index("ix_case_participants_case_id", table_name="case_participants")
    op.drop_table("case_participants")

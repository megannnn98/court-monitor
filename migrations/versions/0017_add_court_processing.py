"""Add court processing state + review-item link to case match candidates.

Revision ID: 0017_add_court_processing
Revises: 0016_add_case_participant
Create Date: 2026-08-12

1. ``review_items.case_match_candidate_id`` — points a court_case_match review
   item at the CaseMatchCandidate it was produced for (one candidate = one
   review task). Nullable because historical rows predate this column, and
   other review item types (``parser_failed``, etc.) keep it NULL.

2. ``court_document_processing`` — per-document pipeline state. Without this,
   a document with zero search results would be re-processed on every
   ``run-all`` invocation. Scoped by ``pipeline_version`` so a future matcher
   change can safely reprocess older documents under a new tag.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_add_court_processing"
down_revision: str | None = "0016_add_case_participant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("review_items") as batch:
        batch.add_column(sa.Column("case_match_candidate_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_review_items_case_match_candidate_id",
            "case_match_candidates",
            ["case_match_candidate_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_review_items_case_match_candidate_id",
        "review_items",
        ["case_match_candidate_id"],
    )

    op.create_table(
        "court_document_processing",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("court", sa.String(length=128), nullable=False),
        sa.Column("pipeline_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["document_id"], ["source_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "pipeline_version", name="uq_court_processing_doc_version"
        ),
    )
    op.create_index(
        "ix_court_document_processing_document_id",
        "court_document_processing",
        ["document_id"],
    )
    op.create_index(
        "ix_court_document_processing_court",
        "court_document_processing",
        ["court"],
    )
    op.create_index(
        "ix_court_document_processing_pipeline_version",
        "court_document_processing",
        ["pipeline_version"],
    )
    op.create_index(
        "ix_court_document_processing_status",
        "court_document_processing",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_court_document_processing_status", table_name="court_document_processing")
    op.drop_index(
        "ix_court_document_processing_pipeline_version",
        table_name="court_document_processing",
    )
    op.drop_index("ix_court_document_processing_court", table_name="court_document_processing")
    op.drop_index(
        "ix_court_document_processing_document_id",
        table_name="court_document_processing",
    )
    op.drop_table("court_document_processing")

    op.drop_index("ix_review_items_case_match_candidate_id", table_name="review_items")
    with op.batch_alter_table("review_items") as batch:
        batch.drop_constraint("fk_review_items_case_match_candidate_id", type_="foreignkey")
        batch.drop_column("case_match_candidate_id")

"""Add Case, PersonCase, CourtEvent models.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-12

Adds models for tracking court cases, their participants, and events.
This enables linking press releases to actual case cards from sud_delo.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_add_case_personcase_courtevent"
down_revision: str | None = "0013_match_candidate_llm_verdict"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Case model - represents a court case
    op.create_table(
        "cases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("court", sa.String(length=255), nullable=False),
        sa.Column("case_number", sa.String(length=100), nullable=False),
        sa.Column("case_uid", sa.String(length=100), nullable=True),
        sa.Column("instance_type", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("decision_at", sa.DateTime(), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("judge", sa.String(length=255), nullable=True),
        sa.Column("first_instance_court", sa.String(length=255), nullable=True),
        sa.Column("first_instance_case_number", sa.String(length=100), nullable=True),
        sa.Column("first_instance_judge", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("court", "case_number", name="uq_case_court_number"),
    )
    op.create_index("ix_cases_case_uid", "cases", ["case_uid"], unique=False)
    op.create_index("ix_cases_court", "cases", ["court"], unique=False)
    op.create_index("ix_cases_case_number", "cases", ["case_number"], unique=False)

    # PersonCase model - links persons to cases with roles
    op.create_table(
        "person_cases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=100), nullable=True),
        sa.Column("articles", sa.Text(), nullable=True),
        sa.Column("material", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("verified_by", sa.String(length=100), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["person_records.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("person_id", "case_id", name="uq_person_case"),
    )
    op.create_index("ix_person_cases_person_id", "person_cases", ["person_id"], unique=False)
    op.create_index("ix_person_cases_case_id", "person_cases", ["case_id"], unique=False)

    # CourtEvent model - tracks events in case lifecycle
    op.create_table(
        "court_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("event_date", sa.DateTime(), nullable=True),
        sa.Column("event_time", sa.String(length=10), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("verified_by", sa.String(length=100), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_court_events_case_id", "court_events", ["case_id"], unique=False)
    op.create_index("ix_court_events_event_type", "court_events", ["event_type"], unique=False)
    op.create_index("ix_court_events_event_date", "court_events", ["event_date"], unique=False)


def downgrade() -> None:
    op.drop_table("court_events")
    op.drop_table("person_cases")
    op.drop_table("cases")

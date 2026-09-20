"""AI review audit for pending ER v2 decisions

Revision ID: w7x8y9z0a1b2
Revises: v6w7x8y9z0a1
Create Date: 2026-09-20 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "w7x8y9z0a1b2"
down_revision: str | Sequence[str] | None = "v6w7x8y9z0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """One row per AI review of a decision (ADR 0020).

    The unique index makes a rerun idempotent: the same decision, the same input, the
    same model and prompt version is answered once. It is partial — a `failed` review
    produced no answer, so the next run may review that input again. A new prompt
    version writes a new row and keeps the earlier decision as history.
    """
    op.create_table(
        "person_resolution_ai_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "decision_id",
            sa.Integer(),
            sa.ForeignKey("person_resolution_decisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column(
            "supporting_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "conflicting_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "candidate_reviews",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("applied_action", sa.String(length=32), nullable=True),
        sa.Column(
            "applied_person_id",
            sa.Integer(),
            sa.ForeignKey("persons.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolution_reason", sa.Text(), nullable=False),
        sa.Column("provider_calls", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # A failed review never produced an answer, so it must not block the same input from
    # being reviewed again; only answered reviews are unique.
    op.create_index(
        "uq_person_resolution_ai_reviews_input",
        "person_resolution_ai_reviews",
        ["decision_id", "input_hash", "model", "prompt_version"],
        unique=True,
        postgresql_where=sa.text("outcome <> 'failed'"),
    )
    op.create_index(
        "ix_person_resolution_ai_reviews_decision", "person_resolution_ai_reviews", ["decision_id"]
    )
    op.create_index(
        "ix_person_resolution_ai_reviews_outcome", "person_resolution_ai_reviews", ["outcome"]
    )


def downgrade() -> None:
    op.drop_index("uq_person_resolution_ai_reviews_input", "person_resolution_ai_reviews")
    op.drop_index("ix_person_resolution_ai_reviews_outcome", "person_resolution_ai_reviews")
    op.drop_index("ix_person_resolution_ai_reviews_decision", "person_resolution_ai_reviews")
    op.drop_table("person_resolution_ai_reviews")

"""add llm_verdict / llm_quote / llm_reasoning to person_match_candidates

Every candidate scores the same because news text carries no birth date or
place, so the score cannot separate a real hit from a namesake. These columns
hold a model's judgement on that question, with the quote it rested on.

Advisory only: the pipeline never lets them change `status` or `score`. NULL
means "not judged" — the LLM is disabled by default and unreachable models
degrade rather than fail.

Revision ID: 0013_match_candidate_llm_verdict
Revises: 0012_match_candidate_context
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_match_candidate_llm_verdict"
down_revision: str | None = "0012_match_candidate_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("person_match_candidates") as batch:
        batch.add_column(sa.Column("llm_verdict", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("llm_quote", sa.Text(), nullable=True))
        batch.add_column(sa.Column("llm_reasoning", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("person_match_candidates") as batch:
        batch.drop_column("llm_reasoning")
        batch.drop_column("llm_quote")
        batch.drop_column("llm_verdict")

"""add namesakes / other_mentions to person_match_candidates

Every candidate the pipeline produces scores the same, because news text
carries no birth date or place and only the name can match (see
known-risks-and-notes.md). Sorting the review queue by score therefore orders
it arbitrarily. These two columns hold the context that does separate
candidates: how many registry records share the surname, and how many other
documents name the same person.

Left NULL rather than defaulted to 0: "never measured" and "no namesakes" are
different claims, and confusing them would put the least-known rows at the top
of the queue. Existing rows fill in on the next generate-matches run.

Revision ID: 0012_match_candidate_context
Revises: 0011_extracted_facts_normalized_value
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_match_candidate_context"
down_revision: str | None = "0011_extracted_facts_normalized_value"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("person_match_candidates") as batch:
        batch.add_column(sa.Column("namesakes", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("other_mentions", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("person_match_candidates") as batch:
        batch.drop_column("other_mentions")
        batch.drop_column("namesakes")

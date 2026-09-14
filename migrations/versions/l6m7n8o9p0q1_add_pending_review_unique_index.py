"""add unique index on pending review records per subject

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-09-14 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "l6m7n8o9p0q1"
down_revision: str | Sequence[str] | None = "k5l6m7n8o9p0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """At most one pending review per (subject_type, subject_id).

    Lets research review tasks be created idempotently with
    INSERT ... ON CONFLICT DO NOTHING, without a check-then-insert race.
    Decided reviews (approved/rejected/...) are not constrained, so a subject
    can be reviewed again later.
    """
    op.create_index(
        "uq_review_records_pending_subject",
        "review_records",
        ["subject_type", "subject_id"],
        unique=True,
        postgresql_where=sa.text("decision = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_review_records_pending_subject", table_name="review_records")

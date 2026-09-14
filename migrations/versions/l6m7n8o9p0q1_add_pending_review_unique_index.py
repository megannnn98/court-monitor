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


_DUPLICATE_PENDING_SQL = """
SELECT subject_type, subject_id, array_agg(id ORDER BY id) AS review_ids
FROM review_records
WHERE decision = 'pending'
GROUP BY subject_type, subject_id
HAVING count(*) > 1
ORDER BY subject_type, subject_id
"""


def upgrade() -> None:
    """At most one pending review per (subject_type, subject_id).

    Lets research review tasks be created idempotently with
    INSERT ... ON CONFLICT DO NOTHING, without a check-then-insert race.
    Decided reviews (approved/rejected/...) are not constrained, so a subject
    can be reviewed again later.

    Existing duplicate pending reviews are NOT resolved automatically: which
    one to keep is a human decision. The migration stops with the list instead
    of a raw unique violation.
    """
    duplicates = op.get_bind().execute(sa.text(_DUPLICATE_PENDING_SQL)).all()
    if duplicates:
        examples = "; ".join(
            f"{row.subject_type}/{row.subject_id}: ids {list(row.review_ids)}"
            for row in duplicates[:10]
        )
        raise RuntimeError(
            "Cannot create uq_review_records_pending_subject: "
            f"{len(duplicates)} subject(s) have more than one pending review "
            f"({examples}). Decide the extra reviews (e.g. UPDATE review_records "
            "SET decision = 'rejected', reviewed_at = now() WHERE id IN (...)) and "
            "rerun `alembic upgrade head`. Inspect with:"
            f"{_DUPLICATE_PENDING_SQL}"
        )

    op.create_index(
        "uq_review_records_pending_subject",
        "review_records",
        ["subject_type", "subject_id"],
        unique=True,
        postgresql_where=sa.text("decision = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_review_records_pending_subject", table_name="review_records")

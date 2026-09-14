"""matching_key is a candidate lookup key, not an identity key

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
Create Date: 2026-09-16 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "o9p0q1r2s3t4"
down_revision: str | Sequence[str] | None = "n8o9p0q1r2s3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """ADR 0012 amendment: two real namesakes may both be active persons.

    The unique index made an equal normalized name an identity proof. It is
    replaced by a plain partial index for candidate lookup; concurrent creation
    is serialized by ER v2 advisory locks on the identity block. Existing
    persons and mention links are not touched.

    `distinct_from_person_id` records a reviewer's KEEP_SEPARATE decision
    ("the linked person is not that person").
    """
    op.drop_index("uq_persons_matching_key_active", table_name="persons")
    op.create_index(
        "ix_persons_matching_key_active",
        "persons",
        ["matching_key"],
        postgresql_where=sa.text("status = 'active'"),
    )
    op.add_column(
        "person_resolution_decisions",
        sa.Column(
            "distinct_from_person_id",
            sa.Integer(),
            sa.ForeignKey("persons.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT matching_key, count(*) FROM persons WHERE status = 'active' "
                "GROUP BY matching_key HAVING count(*) > 1 ORDER BY matching_key LIMIT 5"
            )
        )
        .all()
    )
    if duplicates:
        examples = ", ".join(f"{key!r} x{count}" for key, count in duplicates)
        raise RuntimeError(
            "Cannot restore uq_persons_matching_key_active: active namesakes share "
            f"a matching_key ({examples}). Merge or deactivate them first."
        )
    op.drop_column("person_resolution_decisions", "distinct_from_person_id")
    op.drop_index("ix_persons_matching_key_active", table_name="persons")
    op.create_index(
        "uq_persons_matching_key_active",
        "persons",
        ["matching_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

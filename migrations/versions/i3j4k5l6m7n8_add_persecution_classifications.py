"""Add persecution_classifications table

Revision ID: i3j4k5l6m7n8
Revises: h7m8n9o0p1q2
Create Date: 2026-01-14 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "i3j4k5l6m7n8"
down_revision: str | None = "h7m8n9o0p1q2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "persecution_classifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reasons", JSONB(), nullable=False),
        sa.Column("evidence_types", JSONB(), nullable=False),
        sa.Column("classifier_name", sa.String(), nullable=False),
        sa.Column("classifier_version", sa.String(), nullable=False),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["person_id"], ["persons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "person_id",
            "classifier_name",
            "classifier_version",
            name="uq_persecution_classifications_person_classifier",
        ),
    )
    op.create_index(
        "ix_persecution_classifications_person_id", "persecution_classifications", ["person_id"]
    )
    op.create_index(
        "ix_persecution_classifications_status", "persecution_classifications", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_persecution_classifications_status", table_name="persecution_classifications")
    op.drop_index(
        "ix_persecution_classifications_person_id", table_name="persecution_classifications"
    )
    op.drop_table("persecution_classifications")

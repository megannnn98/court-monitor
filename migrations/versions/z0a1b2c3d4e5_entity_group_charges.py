"""Criminal Code articles of the person entities

Revision ID: z0a1b2c3d4e5
Revises: y9z0a1b2c3d4
Create Date: 2026-09-25 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "z0a1b2c3d4e5"
down_revision: str | Sequence[str] | None = "y9z0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Derived like the groups: every entity rebuild writes it anew."""
    op.create_table(
        "entity_group_charges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("entity_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("extracted_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "publication_id",
            sa.Integer(),
            sa.ForeignKey("parsed_articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("article", sa.String(length=32), nullable=False),
        sa.Column("part", sa.String(length=16), nullable=True),
        sa.Column("clause", sa.String(length=16), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("other_targets", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
    )
    op.create_index("ix_entity_group_charges_group_id", "entity_group_charges", ["group_id"])
    op.create_index("ix_entity_group_charges_article", "entity_group_charges", ["article"])


def downgrade() -> None:
    op.drop_table("entity_group_charges")

"""add person links to mentions and events

Revision ID: h7m8n9o0p1q2
Revises: g1h2i3j4k5l6
Create Date: 2026-09-13 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h7m8n9o0p1q2"
down_revision: str | Sequence[str] | None = "g1h2i3j4k5l6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "entity_mentions",
        sa.Column("person_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_entity_mentions_person_id",
        "entity_mentions",
        "persons",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_entity_mentions_person_id",
        "entity_mentions",
        ["person_id"],
    )

    op.create_table(
        "person_event_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["extracted_events.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "person_id",
            "event_id",
            "role",
            name="uq_person_event_links_person_event_role",
        ),
    )
    op.create_index(
        "ix_person_event_links_person_id",
        "person_event_links",
        ["person_id"],
    )
    op.create_index(
        "ix_person_event_links_event_id",
        "person_event_links",
        ["event_id"],
    )


def downgrade() -> None:
    op.drop_table("person_event_links")
    op.drop_index("ix_entity_mentions_person_id", table_name="entity_mentions")
    op.drop_constraint(
        "fk_entity_mentions_person_id",
        "entity_mentions",
        type_="foreignkey",
    )
    op.drop_column("entity_mentions", "person_id")

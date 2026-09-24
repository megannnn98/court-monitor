"""Person entities gathered from mentions

Revision ID: x8y9z0a1b2c3
Revises: w7x8y9z0a1b2
Create Date: 2026-09-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "x8y9z0a1b2c3"
down_revision: str | Sequence[str] | None = "w7x8y9z0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Derived tables, rebuilt whole by `collect-entities`; nothing else reads them yet."""
    op.create_table(
        "entity_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("variants", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("mention_count", sa.Integer(), nullable=False),
        sa.Column("article_count", sa.Integer(), nullable=False),
        sa.Column("event_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "entity_group_mentions",
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("entity_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "mention_id",
            sa.Integer(),
            sa.ForeignKey("entity_mentions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_entity_group_mentions_mention_id", "entity_group_mentions", ["mention_id"])


def downgrade() -> None:
    op.drop_index("ix_entity_group_mentions_mention_id", table_name="entity_group_mentions")
    op.drop_table("entity_group_mentions")
    op.drop_table("entity_groups")

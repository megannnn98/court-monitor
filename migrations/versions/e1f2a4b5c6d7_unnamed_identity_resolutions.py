"""Operator identifications of unnamed figurants

Revision ID: e1f2a4b5c6d7
Revises: d0e1f2a4b5c6
Create Date: 2026-09-26 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a4b5c6d7"
down_revision: str | Sequence[str] | None = "d0e1f2a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "unnamed_identity_resolutions",
        sa.Column("figurant_key", sa.String(length=64), primary_key=True),
        sa.Column("resolution", sa.String(length=32), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=True),
        sa.Column("existing_person_key", sa.String(length=255), nullable=True),
        sa.Column("rf_name", sa.Text(), nullable=True),
        sa.Column("rf_birth_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "entity_group_unnamed_mentions",
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("entity_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("figurant_key", sa.String(length=64), primary_key=True),
    )
    op.create_index(
        "ix_entity_group_unnamed_mentions_figurant_key",
        "entity_group_unnamed_mentions",
        ["figurant_key"],
    )
    op.execute(
        """
        INSERT INTO unnamed_identity_resolutions
            (figurant_key, resolution, normalized_name, rf_name, rf_birth_date, decided_at)
        SELECT figurant_key, 'rf_entry',
               nullif(split_part(candidate, '|', 1), ''),
               nullif(split_part(candidate, '|', 1), ''),
               nullif(split_part(candidate, '|', 2), '')::date,
               decided_at
        FROM unnamed_decisions
        WHERE decision = 'same'
        ON CONFLICT (figurant_key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO unnamed_identity_resolutions
            (figurant_key, resolution, decided_at)
        SELECT figurant_key, 'no_rf_match', max(decided_at)
        FROM unnamed_decisions
        WHERE decision = 'none'
        GROUP BY figurant_key
        ON CONFLICT (figurant_key) DO NOTHING
        """
    )
    op.execute("DELETE FROM unnamed_decisions WHERE decision IN ('same', 'none')")


def downgrade() -> None:
    op.execute(
        """
        INSERT INTO unnamed_decisions (figurant_key, candidate, decision, decided_at)
        SELECT figurant_key,
               coalesce(rf_name, normalized_name, '') || '|' || coalesce(rf_birth_date::text, ''),
               'same',
               decided_at
        FROM unnamed_identity_resolutions
        WHERE resolution = 'rf_entry'
        ON CONFLICT (figurant_key, candidate) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO unnamed_decisions (figurant_key, candidate, decision, decided_at)
        SELECT figurant_key, '', 'none', decided_at
        FROM unnamed_identity_resolutions
        WHERE resolution = 'no_rf_match'
        ON CONFLICT (figurant_key, candidate) DO NOTHING
        """
    )
    op.drop_index(
        "ix_entity_group_unnamed_mentions_figurant_key",
        table_name="entity_group_unnamed_mentions",
    )
    op.drop_table("entity_group_unnamed_mentions")
    op.drop_table("unnamed_identity_resolutions")

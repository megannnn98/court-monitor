"""add Entity Resolution v2: trigram name indexes and person_resolution_decisions

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-09-15 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "n8o9p0q1r2s3"
down_revision: str | Sequence[str] | None = "m7n8o9p0q1r2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """ADR 0012.

    Trigram GIN indexes narrow fuzzy candidates inside PostgreSQL (no full person
    scan in Python). `person_resolution_decisions` is the provenance of every
    ER v2 decision and the unresolved state of a mention sent to review: the
    mention keeps `person_id` NULL, no placeholder Person is created.
    """
    # pg_trgm is a trusted extension (PostgreSQL 13+): the database owner may create it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_persons_normalized_name_trgm",
        "persons",
        ["normalized_name"],
        postgresql_using="gin",
        postgresql_ops={"normalized_name": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_person_aliases_normalized_text_trgm",
        "person_aliases",
        ["normalized_text"],
        postgresql_using="gin",
        postgresql_ops={"normalized_text": "gin_trgm_ops"},
    )
    op.create_table(
        "person_resolution_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "mention_id",
            sa.Integer(),
            sa.ForeignKey("entity_mentions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("resolver_version", sa.String(32), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "selected_person_id",
            sa.Integer(),
            sa.ForeignKey("persons.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolution_score", sa.Float(), nullable=True),
        sa.Column("decision_margin", sa.Float(), nullable=True),
        sa.Column(
            "reasons",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("identity", postgresql.JSONB(), nullable=False),
        sa.Column(
            "candidates",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("semantic_source", sa.String(32), nullable=False),
        sa.Column("review_action", sa.String(32), nullable=True),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "mention_id",
            "resolver_version",
            name="uq_person_resolution_decisions_mention_version",
        ),
    )
    op.create_index(
        "ix_person_resolution_decisions_status", "person_resolution_decisions", ["status"]
    )
    op.create_index(
        "ix_person_resolution_decisions_selected_person",
        "person_resolution_decisions",
        ["selected_person_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_person_resolution_decisions_selected_person", table_name="person_resolution_decisions"
    )
    op.drop_index("ix_person_resolution_decisions_status", table_name="person_resolution_decisions")
    op.drop_table("person_resolution_decisions")
    op.drop_index("ix_person_aliases_normalized_text_trgm", table_name="person_aliases")
    op.drop_index("ix_persons_normalized_name_trgm", table_name="persons")
    # pg_trgm is left installed: other objects may depend on it.

"""app_settings and entity_model_spend: the entity model chosen in the console, and its costs

Revision ID: n5o6p7q8r9s0
Revises: m4n5o6p7q8r9
Create Date: 2026-10-16 12:00:00.000000

The model of the entity steps was set by `ENTITY_NORMALIZE_MODEL` alone; now the console
chooses it and keeps the choice here. A base without a row keeps the model the
environment names (DeepSeek by default), as before.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "n5o6p7q8r9s0"
down_revision: str | Sequence[str] | None = "m4n5o6p7q8r9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "entity_model_spend",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("command", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("uncosted_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("uncosted_prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("uncosted_completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_entity_model_spend_created_at", "entity_model_spend", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_entity_model_spend_created_at", table_name="entity_model_spend")
    op.drop_table("entity_model_spend")
    op.drop_table("app_settings")

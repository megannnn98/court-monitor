"""The model in the key of every cached answer about an entity

Revision ID: o6p7q8r9s0t1
Revises: n5o6p7q8r9s0
Create Date: 2026-10-16 15:00:00.000000

An answer was found by the question alone (`key`, `input_hash`, `prompt_version`), and a
positive one stuck to the person: when the operator switched the model, the old model's
«accused» and «political» kept answering for it, and the new model's answers to the same
question were dropped on the conflict. With the model in the key each model has answers
of its own; the history stays.

The downgrade keeps, for each question, the newest answer of any model.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "o6p7q8r9s0t1"
down_revision: str | Sequence[str] | None = "n5o6p7q8r9s0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Table → its key without the model.
_KEYS = {
    "entity_role_answers": ("key", "input_hash", "prompt_version"),
    "entity_politics_answers": ("key", "input_hash", "prompt_version"),
    "entity_news_answers": ("key", "input_hash", "prompt_version"),
    "unnamed_answers": ("key", "input_hash", "prompt_version"),
    "entity_name_normalizations": ("key", "prompt_version"),
}


def upgrade() -> None:
    for table, columns in _KEYS.items():
        op.drop_constraint(f"{table}_pkey", table, type_="primary")
        op.create_primary_key(f"{table}_pkey", table, [*columns, "model"])


def downgrade() -> None:
    for table, columns in _KEYS.items():
        same = " AND ".join(f"newer.{column} = older.{column}" for column in columns)
        op.execute(
            f"DELETE FROM {table} older USING {table} newer WHERE {same} "
            "AND (newer.created_at, newer.model) > (older.created_at, older.model)"
        )
        op.drop_constraint(f"{table}_pkey", table, type_="primary")
        op.create_primary_key(f"{table}_pkey", table, list(columns))

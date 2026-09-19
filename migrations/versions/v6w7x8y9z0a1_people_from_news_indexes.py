"""Indexes for "people in the news of a period"

Revision ID: v6w7x8y9z0a1
Revises: u5v6w7x8y9z0
Create Date: 2026-09-19 18:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "v6w7x8y9z0a1"
down_revision: str | Sequence[str] | None = "u5v6w7x8y9z0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The Telegram `/people` query filters articles by `published_at` and walks
    person → event → extraction run → article.

    `entity_mentions.person_id` already has an index; these two paths had none, and a
    foreign key does not create one in PostgreSQL.
    """
    op.create_index("ix_parsed_articles_published_at", "parsed_articles", ["published_at"])
    op.create_index(
        "ix_extracted_events_extraction_run_id", "extracted_events", ["extraction_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_extracted_events_extraction_run_id", table_name="extracted_events")
    op.drop_index("ix_parsed_articles_published_at", table_name="parsed_articles")

"""add extracted_facts.normalized_value and backfill person names

``find_other_mentions`` used to read 2000 rows and filter them in Python
because there was nothing indexable to match on — ``value`` is JSON. That
stopped being correct as soon as the corpus held more than 2000 name facts:
the "other mentions" shown next to an operator's decision became an arbitrary
slice of the table, silently.

The backfill applies the same normalization the application does
(``normalization.normalize_fio``), so facts already collected are findable
without a re-parse of the whole corpus.

Revision ID: 0011_extracted_facts_normalized_value
Revises: 0010_person_records_dedup_birthplace
Create Date: 2026-07-31
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from court_monitor.normalization import normalize_fio

revision: str = "0011_extracted_facts_normalized_value"
down_revision: str | None = "0010_person_records_dedup_birthplace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERSON_NAME_FIELD = "full_name_original"


def _plain_value(raw: str | None) -> str:
    """Unwrap the JSON column into the string the extractor stored.

    Person names are stored as a bare JSON string, so the stored text is
    quoted. Anything that does not decode is used as-is.
    """
    if raw is None:
        return ""
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    return decoded if isinstance(decoded, str) else str(decoded)


def upgrade() -> None:
    with op.batch_alter_table("extracted_facts") as batch:
        batch.add_column(sa.Column("normalized_value", sa.String(length=512), nullable=True))
    op.create_index(
        "ix_extracted_facts_normalized_value",
        "extracted_facts",
        ["normalized_value"],
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, value FROM extracted_facts WHERE field = :field"),
        {"field": PERSON_NAME_FIELD},
    ).fetchall()

    for fact_id, raw_value in rows:
        normalized = normalize_fio(_plain_value(raw_value))
        if not normalized:
            continue
        conn.execute(
            sa.text("UPDATE extracted_facts SET normalized_value = :nv WHERE id = :id"),
            {"nv": normalized, "id": fact_id},
        )


def downgrade() -> None:
    op.drop_index("ix_extracted_facts_normalized_value", table_name="extracted_facts")
    with op.batch_alter_table("extracted_facts") as batch:
        batch.drop_column("normalized_value")

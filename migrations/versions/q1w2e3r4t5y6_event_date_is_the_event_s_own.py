"""`event_date` is the event's own date, not the article's

Revision ID: q1w2e3r4t5y6
Revises: p7q8r9s0t1u2
Create Date: 2026-10-19 18:00:00.000000

`RuleBasedEventExtractor` wrote `document.published_at` into `event_date` for every event
whose text named no other year, so all 16303 dated events carried their article's date to
the second. A detention, an arrest and a case opening therefore looked like they happened on
the day the article came out.

This migration clears the substituted values only, and only where they are provably
substitutions: `event_date` exactly equal to its own article's `published_at`. Those rows
are saved first in `extracted_events_event_date_backup`, so the change is reversible by
hand. Rows whose date was set independently (a value no article could have published with)
are left alone and listed by `event_date_migration_report`.

Running this migration twice changes nothing the second time: after the first run no row
matches the predicate, so the second updates 0 rows.

Nothing else is touched — publication dates, person-event links, evidence spans and event
ids are not modified.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q1w2e3r4t5y6"
down_revision: str | Sequence[str] | None = "p7q8r9s0t1u2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The one predicate this migration acts on: the event's date is its article's date.
_SUBSTITUTED = """
    e.event_date IS NOT DISTINCT FROM a.published_at
"""
_BACKUP = """
    INSERT INTO extracted_events_event_date_backup (event_id, event_date, backed_up_at)
    SELECT e.id, e.event_date, now()
    FROM extracted_events e
    JOIN article_extraction_runs r ON r.id = e.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    WHERE {predicate}
    ON CONFLICT (event_id) DO NOTHING
"""
_REPORT = """
    -- `still_substituted` carries the same `published_at IS NOT NULL` guard as the UPDATE.
    -- Without it `IS NOT DISTINCT FROM` is true for NULL on both sides, so an undated event
    -- of an article that was never dated would be counted as a surviving substitution and
    -- would fail the migration on data this change never touches.
    SELECT count(*) FILTER (
               WHERE e.event_date IS NOT DISTINCT FROM a.published_at
                 AND a.published_at IS NOT NULL
           ) AS still_substituted,
           count(*) FILTER (
               WHERE e.event_date IS NOT NULL
                 AND e.event_date IS DISTINCT FROM a.published_at
           ) AS independently_dated,
           count(*) FILTER (WHERE e.event_date IS NULL) AS undated
    FROM extracted_events e
    JOIN article_extraction_runs r ON r.id = e.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
"""


def upgrade() -> None:
    op.create_table(
        "extracted_events_event_date_backup",
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "backed_up_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    connection = op.get_bind()
    # The article's publication date is unknown for some events; those rows cannot be shown
    # to be substitutions and are left as they are.
    backup = connection.execute(
        sa.text(_BACKUP.format(predicate=_SUBSTITUTED + " AND a.published_at IS NOT NULL"))
    )
    saved = backup.rowcount if backup.rowcount is not None and backup.rowcount >= 0 else 0
    connection.execute(
        sa.text(
            f"""
            UPDATE extracted_events e
            SET event_date = NULL
            FROM article_extraction_runs r, parsed_articles a
            WHERE r.id = e.extraction_run_id
              AND a.id = r.article_id
              AND {_SUBSTITUTED}
              AND a.published_at IS NOT NULL
            """
        )
    )
    counts = connection.execute(sa.text(_REPORT)).mappings().one()
    still = counts["still_substituted"]
    if still:
        raise RuntimeError(f"event_date migration left {still} substituted dates; expected none")
    op.execute(
        sa.text(
            "COMMENT ON TABLE extracted_events_event_date_backup IS "
            "'Dates cleared by migration q1w2e3r4t5y6 (event_date copied from published_at). "
            f"{saved} rows. Restore with an UPDATE ... FROM on event_id.'"
        )
    )


def downgrade() -> None:
    """Put the cleared dates back. Only those this migration cleared are restored."""
    op.execute(
        sa.text(
            """
            UPDATE extracted_events e
            SET event_date = b.event_date
            FROM extracted_events_event_date_backup b
            WHERE b.event_id = e.id AND e.event_date IS NULL
            """
        )
    )
    op.drop_table("extracted_events_event_date_backup")

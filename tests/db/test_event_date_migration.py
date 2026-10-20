"""Migration q1w2e3r4t5y6: only provable substitutions are cleared, and an event of an
article that was never dated is not one of them.

The first version failed the migration on a perfectly valid row. Its check counted an
event with no date whose article has no publication date as a surviving substitution,
because `IS NOT DISTINCT FROM` is true when both sides are NULL — so `upgrade()` raised
on data this change never touches. `parsed_articles.published_at` is nullable by design.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
REVISION = "q1w2e3r4t5y6"
PREVIOUS_REVISION = "p7q8r9s0t1u2"

# A source, a document and an article published on `published` (None: never dated).
FIXTURE = """
INSERT INTO sources (id, name, base_url) VALUES (9001, 'test', 'https://example.test');
INSERT INTO source_documents (id, source_id, external_id, canonical_url, fetched_at,
                             content_type, raw_content)
VALUES (9001, 9001, 'd1', 'https://example.test/d1', now(), 'text/html', ''),
       (9002, 9001, 'd2', 'https://example.test/d2', now(), 'text/html', '');
INSERT INTO parsed_articles (id, document_id, title, published_at, text)
VALUES (9001, 9001, 'недатированная', NULL, 'текст'),
       (9002, 9002, 'датированная', '2026-10-19 09:00:00+00', 'текст');
INSERT INTO article_extraction_runs (id, article_id, article_content_hash, extractor_name,
    extractor_version, normalizer_version, status, started_at)
VALUES (9001, 9001, 'h1', 'rule-based', '1.10.0', '1.0.0', 'succeeded', now()),
       (9002, 9002, 'h2', 'rule-based', '1.10.0', '1.0.0', 'succeeded', now());
-- The row that used to fail the migration: no date, in an article never published.
INSERT INTO extracted_events (id, extraction_run_id, event_type, event_date, start_offset,
    end_offset, confidence, attributes, extractor_name, extractor_version)
VALUES (9001, 9001, 'arrest', NULL, 0, 5, 0.9, '{}', 'rule-based', '1.10.0'),
       -- A provable substitution: its date is its article's, to the second.
       (9002, 9002, 'arrest', '2026-10-19 09:00:00+00', 0, 5, 0.9, '{}', 'rule-based', '1.10.0');
"""


def _alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _dates(session: Session) -> dict[int, Any]:
    return {
        int(event_id): event_date
        for event_id, event_date in session.execute(
            text("SELECT id, event_date FROM extracted_events ORDER BY id")
        ).all()
    }


def test_the_migration_clears_a_substitution_and_keeps_an_undated_article(
    migration_session_factory: sessionmaker[Session],
) -> None:
    engine = migration_session_factory.kw["bind"]
    database_url = engine.url.render_as_string(hide_password=False)

    downgraded = _alembic(database_url, "downgrade", PREVIOUS_REVISION)
    assert downgraded.returncode == 0, downgraded.stderr
    with migration_session_factory.begin() as session:
        session.execute(text(FIXTURE))
        before = _dates(session)
    assert before[9002] is not None, "the fixture must contain a substitution to clear"

    upgraded = _alembic(database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    with migration_session_factory() as session:
        after = _dates(session)
        backed_up = [
            row[0]
            for row in session.execute(
                text("SELECT event_id FROM extracted_events_event_date_backup ORDER BY event_id")
            ).all()
        ]
    # The substitution is cleared and saved; the undated row is left alone and not saved.
    assert after == {9001: None, 9002: None}
    assert backed_up == [9002]


def test_the_migration_is_idempotent(migration_session_factory: sessionmaker[Session]) -> None:
    engine = migration_session_factory.kw["bind"]
    database_url = engine.url.render_as_string(hide_password=False)

    downgraded = _alembic(database_url, "downgrade", PREVIOUS_REVISION)
    assert downgraded.returncode == 0, downgraded.stderr
    with migration_session_factory.begin() as session:
        session.execute(text(FIXTURE))

    first = _alembic(database_url, "upgrade", "head")
    assert first.returncode == 0, first.stderr
    second = _alembic(database_url, "upgrade", "head")
    assert second.returncode == 0, second.stderr

    with migration_session_factory() as session:
        assert _dates(session) == {9001: None, 9002: None}
        # A second pass must not back the same event up twice.
        assert (
            session.execute(
                text("SELECT count(*) FROM extracted_events_event_date_backup")
            ).scalar_one()
            == 1
        )

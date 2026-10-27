"""Whether the shared test database stands at this code's Alembic head."""

from __future__ import annotations

from collections.abc import Iterable


def revision_mismatch(revisions: Iterable[str], head: str) -> str | None:
    """Why a database with these `alembic_version` rows is not at `head`; None when it is.

    `alembic_version` holds one row per applied head: a branch's own migration next to
    this code's head is a second row, so only the single row `head` is accepted."""
    found = sorted(revisions)
    if found == [head]:
        return None
    return (
        f"court_monitor_test is at Alembic revision(s) {found or 'none'}, this code's head "
        f"is {head}. Another branch may have migrated it: with DATABASE_URL set to the test "
        "database, run `alembic downgrade` from that branch, then `alembic upgrade head` here."
    )

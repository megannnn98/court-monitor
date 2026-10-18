"""Guarded helpers for disposable test and evaluation databases."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Engine, text

# The platform's own tables a disposable database is wiped of; each owner names its own
# (`<package>.testing.DISPOSABLE_TABLES`), and the caller passes them all.
PLATFORM_TABLES = ("app_settings", "entity_model_spend")

DISPOSABLE_DATABASE_SUFFIXES = ("_test", "_eval")


class NotDisposableDatabaseError(RuntimeError):
    pass


def require_disposable_database(engine: Engine) -> None:
    name = engine.url.database or ""
    if not name.endswith(DISPOSABLE_DATABASE_SUFFIXES):
        raise NotDisposableDatabaseError(
            f"Refusing to wipe database {name!r}: only databases ending with "
            f"{' or '.join(DISPOSABLE_DATABASE_SUFFIXES)} may be truncated"
        )


def truncate_disposable_tables(engine: Engine, tables: Sequence[str]) -> None:
    """Wipe `tables` of a disposable database in one statement, their sequences reset."""
    require_disposable_database(engine)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE"))

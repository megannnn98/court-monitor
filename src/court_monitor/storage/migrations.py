"""Run Alembic migrations via the public API (used by CLI ``init-db``/``migrate``)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_alembic_config(database_url: str | None = None) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("prepend_sys_path", str(REPO_ROOT / "src"))
    if database_url:
        cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def upgrade_head(database_url: str | None = None) -> None:
    cfg = _make_alembic_config(database_url)
    command.upgrade(cfg, "head")


def current(database_url: str | None = None) -> None:
    cfg = _make_alembic_config(database_url)
    command.current(cfg)


def revision_status(database_url: str | None = None) -> tuple[str | None, str | None]:
    """Return ``(applied_revision, head_revision)``.

    Lets callers detect a database that is behind the migration scripts. That
    drift is otherwise invisible until an ORM query touches a column the
    pending migration was supposed to add, which surfaces as a bare
    ``OperationalError: no such column`` in the middle of a pipeline run.
    """
    from court_monitor.storage.db import make_engine  # noqa: PLC0415 - avoids an import cycle

    cfg = _make_alembic_config(database_url)
    head = ScriptDirectory.from_config(cfg).get_current_head()

    engine = make_engine(database_url)
    with engine.connect() as conn:
        applied = MigrationContext.configure(conn).get_current_revision()
    return applied, head

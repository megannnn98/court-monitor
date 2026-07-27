"""Run Alembic migrations via the public API (used by CLI ``init-db``/``migrate``)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

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

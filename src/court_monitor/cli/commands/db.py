"""Database lifecycle and environment diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from sqlalchemy import inspect, text

from court_monitor import __version__
from court_monitor.cli._shared import REPO_ROOT, bootstrap_logging, safe_url
from court_monitor.config.loader import load_monitoring
from court_monitor.config.registry import load_registry
from court_monitor.config.settings import settings
from court_monitor.observability import get_logger
from court_monitor.storage import repository as repo
from court_monitor.storage.db import make_engine, make_session_factory
from court_monitor.storage.migrations import revision_status, upgrade_head


def init_db() -> None:
    """Create the database schema (runs Alembic upgrade head)."""
    bootstrap_logging()
    log = get_logger("cli.init_db")
    upgrade_head(settings.database_url)
    log.info("db.ready", url=safe_url(settings.database_url))
    typer.echo("Database schema is ready.")


def migrate() -> None:
    """Apply database migrations (alias of init-db at this stage)."""
    bootstrap_logging()
    upgrade_head(settings.database_url)
    typer.echo("Migrations applied.")


def show_config() -> None:
    """Show current database configuration."""
    bootstrap_logging()
    db_url = settings.database_url
    is_sqlite = db_url.startswith("sqlite")

    typer.echo(f"Database URL: {safe_url(db_url)}")

    if is_sqlite:
        db_path = db_url.replace("sqlite:///", "").replace("sqlite://", "")
        db_file = Path(db_path).resolve()
        typer.echo(f"Absolute path: {db_file}")
        typer.echo(f"SQLite exists: {'yes' if db_file.exists() else 'no'}")

    # Alembic current revision
    try:
        from alembic import command as alembic_cmd  # noqa: PLC0415
        from alembic.config import Config as AlembicConfig  # noqa: PLC0415

        alembic_cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
        alembic_cfg.set_main_option("prepend_sys_path", str(REPO_ROOT / "src"))
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)

        alembic_cmd.current(alembic_cfg)
        typer.echo("Alembic revision: ✓ applied")
    except Exception as exc:
        typer.echo(f"Alembic revision: error ({exc})")

    # Settings source
    import os  # noqa: PLC0415

    env_val = os.environ.get("CM_DATABASE_URL")
    if env_val:
        typer.echo("Settings source: env var CM_DATABASE_URL")
    elif Path(".env").exists():
        typer.echo("Settings source: .env file")
    else:
        typer.echo("Settings source: default value")


def _doctor_check_sqlite() -> list[str]:
    """Check SQLite file existence and connectivity."""
    db_url = settings.database_url
    if not db_url.startswith("sqlite"):
        return []

    problems: list[str] = []
    db_path = db_url.replace("sqlite:///", "").replace("sqlite://", "")
    db_file = Path(db_path)
    exists = db_file.exists()
    typer.echo(f"database_exists: {'✓' if exists else '✗'} ({db_path})")
    if not exists:
        problems.append(f"Database file does not exist: {db_path}")

    try:
        engine = make_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        typer.echo("connection: ✓")
    except Exception as exc:
        typer.echo(f"connection: ✗ ({exc})")
        problems.append(f"db connection failed: {exc}")
    return problems


def _doctor_check_tables() -> list[str]:
    """Check that required tables exist."""
    problems: list[str] = []
    try:
        engine = make_engine()
        tables = inspect(engine).get_table_names()
        expected = {
            "source_documents",
            "extracted_facts",
            "person_records",
            "person_match_candidates",
            "review_items",
            "audit_log",
            "jobs",
        }
        missing = expected - set(tables)
        if missing:
            typer.echo(f"tables: ✗ (missing: {', '.join(sorted(missing))})")
            problems.append(f"Missing tables: {', '.join(sorted(missing))}")
        else:
            typer.echo(f"tables: ✓ ({len(tables)} tables)")
    except Exception as exc:
        typer.echo(f"tables: ✗ ({exc})")
        problems.append(f"table inspection failed: {exc}")
    return problems


def _doctor_check_alembic() -> list[str]:
    """Check that the database is migrated up to the latest revision.

    A database left behind the scripts still answers "SELECT 1" and still has
    all its old tables, so every other check here passes while the next
    pipeline run dies on a missing column. Treat the drift as a problem.
    """
    try:
        applied, head = revision_status(settings.database_url)
    except Exception as exc:
        typer.echo(f"alembic_current: ✗ ({exc})")
        return [f"alembic revision check failed: {exc}"]

    if applied == head:
        typer.echo(f"alembic_current: ✓ ({applied})")
        return []

    typer.echo(f"alembic_current: ✗ (применена {applied or 'нет'}, ожидается {head})")
    return [
        f"Database is behind migrations: applied={applied or 'none'}, head={head}. "
        "Run: court-monitor migrate"
    ]


def _doctor_check_repository() -> list[str]:
    """Check repository query works."""
    try:
        factory = make_session_factory(make_engine())
        with factory() as session:
            doc_count = repo.count_documents(session)
            fact_count = repo.count_facts(session)
        typer.echo(f"repository: ✓ (docs={doc_count}, facts={fact_count})")
    except Exception as exc:
        typer.echo(f"repository: ✗ ({exc})")
        return [f"repository query failed: {exc}"]
    return []


def _doctor_check_config() -> list[str]:
    """Check config files (monitoring, registry)."""
    problems: list[str] = []
    try:
        monitoring = load_monitoring()
        typer.echo(
            f"monitoring: ✓ ({len(monitoring.article_set())} articles, "
            f"{len(monitoring.keyword_set())} keywords)"
        )
    except Exception as exc:  # pragma: no cover
        typer.echo(f"monitoring: ✗ ({exc})")
        problems.append(f"monitoring config error: {exc}")

    try:
        registry = load_registry()
        typer.echo(f"registry: ✓ ({len(registry)} sources)")
    except Exception as exc:  # pragma: no cover
        typer.echo(f"registry: ✗ ({exc})")
        problems.append(f"registry error: {exc}")
    return problems


def _doctor_check_settings_source() -> None:
    """Show where settings are loaded from."""
    import os  # noqa: PLC0415

    env_val = os.environ.get("CM_DATABASE_URL")
    if env_val:
        typer.echo("settings_source: env var CM_DATABASE_URL")
    elif Path(".env").exists():
        typer.echo("settings_source: .env file")
    else:
        typer.echo("settings_source: default")


def doctor() -> None:
    """Sanity-check the environment: config, DB, migrations, repository."""
    bootstrap_logging()
    typer.echo(f"court-monitor {__version__}")
    typer.echo(f"python: {sys.version.split()[0]}")
    typer.echo(f"database_url: {safe_url(settings.database_url)}")

    # Each check reports its own findings rather than mutating a shared list,
    # so a check can be run and asserted on in isolation.
    problems: list[str] = []
    for check in (
        _doctor_check_sqlite,
        _doctor_check_tables,
        _doctor_check_alembic,
        _doctor_check_repository,
        _doctor_check_config,
    ):
        problems.extend(check())
    _doctor_check_settings_source()

    if problems:
        typer.echo("\nProblems:")
        for p in problems:
            typer.echo(f"  ✗ {p}")
        raise typer.Exit(code=1)
    typer.echo("\n✓ All checks passed.")


def register(app: typer.Typer) -> None:
    app.command()(init_db)
    app.command()(migrate)
    app.command(name="show-config")(show_config)
    app.command()(doctor)

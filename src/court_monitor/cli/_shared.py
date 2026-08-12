"""Helpers used by more than one command group."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import typer

from court_monitor.config.settings import settings
from court_monitor.observability import configure_logging, configure_logging_with_mode
from court_monitor.storage.migrations import revision_status

REPO_ROOT = Path(__file__).resolve().parents[3]


def bootstrap_logging() -> None:
    configure_logging(settings.log_level)


def bootstrap_logging_with_mode(*, verbose: bool = False, json_logs: bool = False) -> None:
    """Bootstrap structlog with the appropriate output mode.

    Parameters:
        verbose: If True, use verbose human-readable mode.
        json_logs: If True, use raw JSON mode (machine-readable).
                   Overrides verbose. Default (both False): human mode.
    """
    if json_logs:
        configure_logging_with_mode(settings.log_level, mode="json")
    elif verbose:
        configure_logging_with_mode(settings.log_level, mode="verbose")
    else:
        configure_logging_with_mode("WARNING", mode="human")


def safe_url(url: str) -> str:
    if "://" in url and "@" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


def db_display_path(url: str) -> str:
    """Human-readable DB location.

    For SQLite returns the absolute filesystem path (resolving relative paths
    against the current directory) so it's obvious where data lands. Other
    backends get the credential-masked URL via :func:`safe_url`.
    """
    if url.startswith("sqlite"):
        body = url.split("sqlite:///", 1)[1] if "sqlite:///" in url else ""
        if not body or body == ":memory:" or "memory" in url:
            return ":memory:"
        return str(Path(body).resolve())
    return safe_url(url)


def require_current_schema() -> None:
    """Abort before doing any work if the database is behind the migrations.

    A database on an older revision still opens, still answers SELECT 1 and
    still has every table it used to, so nothing complains until an ORM query
    touches a column a pending migration was supposed to add. In ``run-all``
    that moment is ``generate_matches`` — the very last step, after every
    source has already been fetched over the network and written.

    Checking up front costs one query and turns that into one actionable line.
    """
    try:
        applied, head = revision_status(settings.database_url)
    except Exception:  # pragma: no cover - never block work over a failed check
        return
    if applied == head:
        return

    typer.secho(
        f"База данных отстаёт от миграций: применена {applied or 'нет'}, ожидается {head}.\n"
        "Выполните: court-monitor migrate",
        fg=typer.colors.RED,
        bold=True,
        err=True,
    )
    raise typer.Exit(code=1)


@contextmanager
def maybe_dry_run_session(engine, *, dry_run: bool):
    """Session that rolls back instead of committing when ``dry_run`` is set."""
    from court_monitor.storage.db import make_session_factory, session_scope  # noqa: PLC0415

    if not dry_run:
        with session_scope(engine) as session:
            yield session
        return

    session = make_session_factory(engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()

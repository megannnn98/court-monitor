"""court-monitor CLI entrypoint."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from court_monitor import __version__
from court_monitor.config.loader import (
    get_source,
    load_monitoring,
    load_sources,
)
from court_monitor.config.registry import (
    DEFAULT_REGISTRY_PATH,
    ImportPreview,
    load_registry,
    merge_registry,
    normalize_rows,
    save_registry,
    set_entry_status,
)
from court_monitor.config.settings import settings
from court_monitor.matching.candidates import generate_matches
from court_monitor.observability import configure_logging, get_logger
from court_monitor.services import (
    SourceStats,
    process_registry_source,
    process_source,
)
from court_monitor.sources.airtable_registry import (
    DEFAULT_REGISTRY_VIEW_URL,
    parse_airtable_shared_view,
    parse_registry_csv,
    render_shared_view,
)
from court_monitor.sources.probe import probe_source
from court_monitor.storage import repository as repo
from court_monitor.storage.db import make_engine, make_session_factory, session_scope

from ._shared import require_current_schema
from .commands import db as _db_commands
from .commands import documents as _documents_commands
from .commands import records as _records_commands
from .commands import review as _review_commands
from .commands.fedsfm import _handle_fedsfm

app = typer.Typer(
    name="court-monitor",
    help="Semi-automated OSINT monitoring of criminal cases.",
    no_args_is_help=True,
    add_completion=False,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# Default fixture location for a registry telegram source (no-network mode).
# Anchored to the repo rather than the CWD so the command works from anywhere.
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "telegram"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"court-monitor {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[  # noqa: ARG001
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the application version and exit.",
        ),
    ] = False,
) -> None:
    """court-monitor — OSINT monitoring of criminal cases."""


def _bootstrap_logging() -> None:
    configure_logging(settings.log_level)


# ---------------------------------------------------------------------------
# Source registry: import + check
# ---------------------------------------------------------------------------


@app.command(name="import-source-registry")
def import_source_registry(
    from_airtable: Annotated[
        bool,
        typer.Option("--from-airtable", help="Read the public Airtable shared view."),
    ] = False,
    from_csv: Annotated[
        str | None,
        typer.Option("--from-csv", help="Import a CSV exported from the shared view."),
    ] = None,
    view_url: Annotated[
        str,
        typer.Option(help="Airtable shared view URL."),
    ] = DEFAULT_REGISTRY_VIEW_URL,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview only; do not write (default behaviour)."),
    ] = True,
    registry_path: Annotated[
        str, typer.Option(help="Local registry YAML to read/write.")
    ] = DEFAULT_REGISTRY_PATH,
) -> None:
    """Import the source list from Airtable or a CSV export (preview by default)."""
    _bootstrap_logging()
    log = get_logger("cli.import_registry")

    if not from_airtable and not from_csv:
        typer.echo(
            "Укажите источник: --from-airtable или --from-csv <file>. "
            "По умолчанию команда работает в режиме --dry-run.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        if from_csv:
            raw_rows = parse_registry_csv(from_csv)
            origin = f"csv:{from_csv}"
        else:
            log.info("airtable.render.start", url=view_url)
            html = render_shared_view(view_url)
            raw_rows = parse_airtable_shared_view(html)
            origin = f"airtable:{view_url}"
    except Exception as exc:
        typer.echo(f"Не удалось прочитать реестр: {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    entries, norm_stats = normalize_rows(raw_rows)
    existing = load_registry(registry_path)
    merged, preview = merge_registry(existing, entries, stats=norm_stats)

    typer.echo(f"Источник: {origin}")
    typer.echo(preview.render())

    if dry_run:
        typer.echo("\n(режим --dry-run: реестр не изменён)")
        return

    save_registry(merged, registry_path)
    typer.echo(f"\nРеестр записан: {registry_path} ({len(merged)} источников)")


@app.command(name="check-sources")
def check_sources(
    registry_path: Annotated[
        str, typer.Option(help="Local registry YAML.")
    ] = DEFAULT_REGISTRY_PATH,
) -> None:
    """Probe every source in the local registry (one polite HTTP request each)."""
    _bootstrap_logging()
    log = get_logger("cli.check_sources")
    entries = load_registry(registry_path)
    if not entries:
        typer.echo(f"Реестр пуст или отсутствует: {registry_path}")
        typer.echo("Сначала выполните: court-monitor import-source-registry --from-airtable")
        raise typer.Exit(code=1)

    typer.echo(f"{'ID':24} {'Тип':10} {'Статус':26} {'HTTP':>5}  Примечание")
    updated = entries
    for entry in entries:
        result = probe_source(entry)
        log.info(
            "probe.result",
            source=entry.id,
            status=result.status,
            http=result.http_status,
            note=result.note,
        )
        typer.echo(
            f"{entry.id[:24]:24} {entry.source_type[:10]:10} {result.status:26} "
            f"{result.http_status:>5}  {result.note}"
        )
        updated = set_entry_status(updated, entry.id, status=result.status, reason=result.note)

    save_registry(updated, registry_path)


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------


def _registry_entry_or_none(name: str):
    for entry in load_registry():
        if entry.id == name:
            return entry
    return None


def _fixture_path_for(entry) -> Path | None:
    if entry.source_type == "telegram":
        return FIXTURE_DIR / f"tg_preview_{entry.id}.html"
    return None


@app.command()
def fetch_source(
    name: Annotated[str, typer.Argument(help="Registry source id (or legacy sources.yaml name)")],
    *,
    live: Annotated[
        bool, typer.Option("--live", help="Fetch the real source over HTTP (default: fixture).")
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Cap the number of materials fetched."),
    ] = None,
    no_parse: Annotated[
        bool, typer.Option("--no-parse", help="Only fetch; leave documents in pending state.")
    ] = False,
    file: Annotated[
        str | None, typer.Option("--file", help="Import from a local file (XML, DBF, ZIP, CSV).")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview only; do not write to DB.")
    ] = False,
) -> None:
    """Fetch data from a source (RFM, sudrf, etc.)."""
    _bootstrap_logging()
    # Checked before the fetch: the RFM list is a 4 MB download parsed into 21k
    # rows, and writing them is what first touches a column a pending migration
    # would have added.
    require_current_schema()

    if name == "fedsfm":
        _handle_fedsfm(file=file, live=live, dry_run=dry_run)
        return

    _fetch_source_legacy(name, live=live, no_parse=no_parse, limit=limit, dry_run=dry_run)


def _fetch_source_legacy(
    name: str,
    *,
    live: bool = False,
    no_parse: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
) -> None:
    monitoring = load_monitoring()

    entry = _registry_entry_or_none(name)
    if entry is not None:
        fixture_path = _fixture_path_for(entry)
        engine = make_engine()
        with _maybe_dry_run_session(engine, dry_run=dry_run) as session:
            stats = process_registry_source(
                session,
                entry,
                monitoring,
                live=live,
                limit=limit,
                fixture_path=str(fixture_path) if fixture_path else None,
                parse_immediately=not no_parse,
            )
        typer.echo(_render_registry_stats(stats, entry.id, live=live))
        if dry_run:
            typer.echo("\n(режим --dry-run: данные не записаны)")
        return

    src = get_source(name)
    if src is None or not src.enabled:
        typer.echo(
            f"Источник «{name}» не найден ни в реестре, ни в config/sources.yaml.",
            err=True,
        )
        raise typer.Exit(code=1)
    engine = make_engine()
    with _maybe_dry_run_session(engine, dry_run=dry_run) as session:
        stats = process_source(
            session,
            src,
            monitoring,
            parse_immediately=not no_parse,
            limit=limit,
        )
    typer.echo(
        f"{name}: fetched={stats.fetched} new={stats.new_documents} "
        f"duplicates={stats.duplicates} parsed={stats.parsed} "
        f"irrelevant={stats.irrelevant} failed={stats.failed} blocked={stats.blocked}"
    )
    if dry_run:
        typer.echo("\n(режим --dry-run: данные не записаны)")


@contextmanager
def _maybe_dry_run_session(engine, *, dry_run: bool):
    if not dry_run:
        with session_scope(engine) as session:
            yield session
        return

    factory = make_session_factory(engine)
    session = factory()
    try:
        yield session
        session.rollback()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _render_registry_stats(stats, source_id: str, *, live: bool) -> str:
    mode = "live" if live else "fixture"
    return (
        f"{source_id} ({mode}): fetched={stats.fetched} new={stats.new_documents} "
        f"already_exists={stats.duplicates} parsed={stats.parsed} "
        f"irrelevant={stats.irrelevant} failed={stats.failed} blocked={stats.blocked}"
    )


@app.command(name="fetch-all")
def fetch_all() -> None:
    """Fetch from all enabled legacy sources (config/sources.yaml)."""
    _bootstrap_logging()
    monitoring = load_monitoring()
    engine = make_engine()
    totals = SourceStats()
    for src in (s for s in load_sources() if s.enabled):
        try:
            with session_scope(engine) as session:
                totals.accumulate(process_source(session, src, monitoring))
        except Exception as exc:
            typer.echo(f"  {src.name}: ERROR {type(exc).__name__}: {exc}", err=True)
            totals.failed += 1
    typer.echo(f"TOTAL {totals.summary()}")


def _stats_color(stats, *, skipped: bool = False) -> str:
    """Green = clean, yellow = needs a look (blocked/failed/skipped), never red here —
    red is reserved for hard exceptions (a source that crashed, not just found nothing)."""
    if stats.failed > 0 or stats.blocked > 0 or skipped:
        return typer.colors.YELLOW
    return typer.colors.GREEN


@app.command(name="run-all")
def run_all(
    live: Annotated[
        bool,
        typer.Option("--live", help="Fetch real sources over HTTP instead of fixtures."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose", "-v", help="Show detailed per-document JSON logs (normal log level)."
        ),
    ] = False,
) -> None:
    """Fetch everything (sudrf sources + Telegram registry channels), then generate matches.

    Fixtures by default (no network); pass --live to hit real sources. By
    default, per-document INFO/WARNING logs are suppressed (only genuine
    errors, with traceback, still print) so the per-source summary lines
    below are actually readable — pass --verbose to see the full structured
    JSON log.
    """
    configure_logging(settings.log_level if verbose else "ERROR")
    require_current_schema()
    monitoring = load_monitoring()
    engine = make_engine()
    totals = SourceStats()

    typer.secho(
        f"База данных: {_db_display_path(settings.database_url)}",
        fg=typer.colors.WHITE,
        bold=True,
    )

    typer.secho("=== Sudrf-источники ===", fg=typer.colors.CYAN, bold=True)
    sudrf_sources = [s for s in load_sources() if s.enabled]
    if not sudrf_sources:
        typer.secho("  (нет включённых источников в config/sources.yaml)", dim=True)
    for src in sudrf_sources:
        try:
            with session_scope(engine) as session:
                stats = process_source(session, src, monitoring)
                totals.accumulate(stats)
            typer.secho(f"  ✓ {src.name}: {stats.summary()}", fg=_stats_color(stats))
        except Exception as exc:
            typer.secho(
                f"  ✗ {src.name}: ОШИБКА {type(exc).__name__}: {exc}",
                fg=typer.colors.RED,
                bold=True,
                err=True,
            )
            totals.failed += 1

    typer.secho("\n=== Telegram-каналы ===", fg=typer.colors.CYAN, bold=True)
    registry_entries = [e for e in load_registry() if e.enabled]
    if not registry_entries:
        typer.secho("  (нет включённых каналов в config/source_registry.yaml)", dim=True)
    for entry in registry_entries:
        try:
            fixture_path = _fixture_path_for(entry)
            # _fixture_path_for returns None for any non-telegram source_type too
            # (unsupported by _build_registry_adapter) — only call it "fixture
            # missing" when that's actually why, not for an unrelated reason.
            fixture_missing = (
                not live
                and entry.source_type == "telegram"
                and (fixture_path is None or not fixture_path.exists())
            )
            with session_scope(engine) as session:
                stats = process_registry_source(
                    session,
                    entry,
                    monitoring,
                    live=live,
                    fixture_path=str(fixture_path) if fixture_path else None,
                )
                totals.accumulate(stats)
            note = " (нет сохранённой fixture — пропущено)" if fixture_missing else ""
            typer.secho(
                f"  ✓ {entry.id}: {stats.summary()}{note}",
                fg=_stats_color(stats, skipped=fixture_missing),
            )
        except Exception as exc:
            typer.secho(
                f"  ✗ {entry.id}: ОШИБКА {type(exc).__name__}: {exc}",
                fg=typer.colors.RED,
                bold=True,
                err=True,
            )
            totals.failed += 1

    typer.secho("\n=== Итого: fetch + parse ===", fg=typer.colors.CYAN, bold=True)
    totals_color = typer.colors.YELLOW if totals.needs_attention else typer.colors.GREEN
    typer.secho(f"  {totals.summary()}", fg=totals_color, bold=True)

    with session_scope(engine) as session:
        match_stats = generate_matches(session)
    typer.secho("\n=== Совпадения (generate-matches) ===", fg=typer.colors.CYAN, bold=True)
    matches_color = typer.colors.RED if match_stats["errors"] else typer.colors.GREEN
    typer.secho(
        f"  создано={match_stats['candidates_created']} "
        f"уже_было={match_stats['already_existed']} "
        f"без_кандидата={match_stats['no_candidates']} "
        f"ошибок={match_stats['errors']}",
        fg=matches_color,
        bold=True,
    )

    typer.secho(
        f"\nДанные сохранены в БД: {_db_display_path(settings.database_url)}",
        fg=typer.colors.WHITE,
        bold=True,
    )


@app.command(name="list-audit-log")
def list_audit_log_cmd(
    object_type: Annotated[
        str | None, typer.Option("--type", help="Filter: match_candidate / review_item.")
    ] = None,
    object_id: Annotated[int | None, typer.Option("--id", help="Filter by object id.")] = None,
    limit: Annotated[int, typer.Option(help="Max rows to print.")] = 50,
) -> None:
    """Show the append-only trail of operator decisions."""
    _bootstrap_logging()
    factory = make_session_factory(make_engine())
    with factory() as session:
        entries = repo.list_audit_log(
            session, object_type=object_type, object_id=object_id, limit=limit
        )
        total = repo.count_audit_log(session, object_type=object_type, object_id=object_id)

    typer.echo(f"Всего записей аудита: {total}")
    typer.echo(f"{'Когда':20}  {'Кто':16}  {'Действие':22}  {'Объект':24}  Изменение")
    typer.echo("-" * 110)
    for e in entries:
        when = e.created_at.strftime("%Y-%m-%d %H:%M:%S") if e.created_at else "-"
        obj = f"{e.object_type}#{e.object_id}"
        change = f"{e.old_value_json or '-'} -> {e.new_value_json or '-'}"
        typer.echo(f"{when:20}  {e.actor[:16]:16}  {e.action[:22]:22}  {obj[:24]:24}  {change}")


@app.command(name="run-web")
def run_web(
    host: Annotated[str | None, typer.Option("--host", help="Bind address.")] = None,
    port: Annotated[int | None, typer.Option("--port", help="Port.")] = None,
    reload: Annotated[bool, typer.Option("--reload", help="Auto-reload on code changes.")] = False,
) -> None:
    """Serve the operator web UI (review queue, documents, statistics).

    Binds to loopback unless told otherwise: the review pages change match
    decisions and there is no authentication yet (D-007).
    """
    import uvicorn  # noqa: PLC0415 - keeps CLI startup free of the server import

    bind_host = host or settings.web_host
    bind_port = port or settings.web_port
    if bind_host not in {"127.0.0.1", "localhost", "::1"}:
        typer.secho(
            f"ВНИМАНИЕ: интерфейс слушает {bind_host} — он доступен не только с этой машины, "
            "а аутентификации пока нет (D-007). Любой, кто дотянется до порта, сможет "
            "подтверждать и отклонять совпадения от вашего имени.",
            fg=typer.colors.YELLOW,
            bold=True,
            err=True,
        )
    typer.secho(f"http://{bind_host}:{bind_port}", fg=typer.colors.CYAN, bold=True)
    uvicorn.run("court_monitor.web.app:app", host=bind_host, port=bind_port, reload=reload)


def _safe_url(url: str) -> str:
    if "://" in url and "@" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


def _db_display_path(url: str) -> str:
    """Human-readable DB location.

    For SQLite returns the absolute filesystem path (resolving relative paths
    against the current directory) so it's obvious where data lands. Other
    backends get the credential-masked URL via :func:`_safe_url`.
    """
    if url.startswith("sqlite"):
        body = url.split("sqlite:///", 1)[1] if "sqlite:///" in url else ""
        if not body or body == ":memory:" or "memory" in url:
            return ":memory:"
        return str(Path(body).resolve())
    return _safe_url(url)


# ---------------------------------------------------------------------------
# Person matching: split into helpers
# ---------------------------------------------------------------------------


# Command groups live in cli/commands/; each attaches its commands under their
# existing flat names so `court-monitor <command>` is unchanged.
_db_commands.register(app)
_documents_commands.register(app)
_records_commands.register(app)
_review_commands.register(app)

__all__ = ["app", "ImportPreview"]


if __name__ == "__main__":
    app()

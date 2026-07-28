"""court-monitor CLI entrypoint."""

from __future__ import annotations

import getpass
import hashlib
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from selectolax.parser import HTMLParser
from sqlalchemy import text

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
from court_monitor.domain.models import FetchHealth
from court_monitor.matching.candidates import generate_matches
from court_monitor.observability import configure_logging, correlation_scope, get_logger
from court_monitor.services import (
    import_rfm_records,
    process_pending,
    process_registry_source,
    process_source,
    reprocess,
)
from court_monitor.sources.airtable_registry import (
    DEFAULT_REGISTRY_VIEW_URL,
    parse_airtable_shared_view,
    parse_registry_csv,
    render_shared_view,
)
from court_monitor.sources.fedsfm import load_fixture_rows, parse_file
from court_monitor.sources.http_client import HttpClient
from court_monitor.sources.probe import probe_source
from court_monitor.storage import repository as repo
from court_monitor.storage.db import make_engine, make_session_factory, session_scope
from court_monitor.storage.migrations import upgrade_head

app = typer.Typer(
    name="court-monitor",
    help="Semi-automated OSINT monitoring of criminal cases.",
    no_args_is_help=True,
    add_completion=False,
)

# Default fixture location for a registry telegram source (no-network mode).
FIXTURE_DIR = Path("tests/fixtures/telegram")
REPO_ROOT = Path(__file__).resolve().parents[3]


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


@app.command()
def init_db() -> None:
    """Create the database schema (runs Alembic upgrade head)."""
    _bootstrap_logging()
    log = get_logger("cli.init_db")
    upgrade_head(settings.database_url)
    log.info("db.ready", url=_safe_url(settings.database_url))
    typer.echo("Database schema is ready.")


@app.command()
def migrate() -> None:
    """Apply database migrations (alias of init-db at this stage)."""
    _bootstrap_logging()
    upgrade_head(settings.database_url)
    typer.echo("Migrations applied.")


@app.command(name="show-config")
def show_config() -> None:
    """Show current database configuration."""
    _bootstrap_logging()
    db_url = settings.database_url
    is_sqlite = db_url.startswith("sqlite")

    typer.echo(f"Database URL: {_safe_url(db_url)}")

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


def _doctor_check_sqlite(problems: list[str]) -> None:
    """Check SQLite file existence and connectivity."""
    db_url = settings.database_url
    if not db_url.startswith("sqlite"):
        return

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


def _doctor_check_tables(problems: list[str]) -> None:
    """Check that required tables exist."""
    try:
        engine = make_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            )
            tables = [row[0] for row in result]
            expected = {
                "source_documents",
                "extracted_facts",
                "person_records",
                "person_match_candidates",
            }
            missing = expected - set(tables)
            if missing:
                typer.echo(f"tables: ✗ (missing: {', '.join(sorted(missing))})")
                problems.append(f"Missing tables: {', '.join(sorted(missing))}")
            else:
                typer.echo(f"tables: ✓ ({len(tables)} tables)")
    except Exception as exc:
        typer.echo(f"tables: ✗ ({exc})")


def _doctor_check_alembic() -> None:
    """Check Alembic current revision."""

    from alembic import command as alembic_cmd  # noqa: PLC0415
    from alembic.config import Config as AlembicConfig  # noqa: PLC0415

    try:
        alembic_cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
        alembic_cfg.set_main_option("prepend_sys_path", str(REPO_ROOT / "src"))
        alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)

        alembic_cmd.current(alembic_cfg)
        typer.echo("alembic_current: ✓")
    except Exception as exc:
        typer.echo(f"alembic_current: ✗ ({exc})")


def _doctor_check_repository(problems: list[str]) -> None:
    """Check repository query works."""
    try:
        factory = make_session_factory(make_engine())
        with factory() as session:
            doc_count = repo.count_documents(session)
            fact_count = repo.count_facts(session)
        typer.echo(f"repository: ✓ (docs={doc_count}, facts={fact_count})")
    except Exception as exc:
        typer.echo(f"repository: ✗ ({exc})")
        problems.append(f"repository query failed: {exc}")


def _doctor_check_config(problems: list[str]) -> None:
    """Check config files (monitoring, registry)."""
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


@app.command()
def doctor() -> None:
    """Sanity-check the environment: config, DB, migrations, repository."""
    _bootstrap_logging()
    problems: list[str] = []

    typer.echo(f"court-monitor {__version__}")
    typer.echo(f"python: {sys.version.split()[0]}")
    typer.echo(f"database_url: {_safe_url(settings.database_url)}")

    _doctor_check_sqlite(problems)
    _doctor_check_tables(problems)
    _doctor_check_alembic()
    _doctor_check_repository(problems)
    _doctor_check_config(problems)
    _doctor_check_settings_source()

    if problems:
        typer.echo("\nProblems:")
        for p in problems:
            typer.echo(f"  ✗ {p}")
        raise typer.Exit(code=1)
    typer.echo("\n✓ All checks passed.")


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
    rows_for_save: list[tuple[object, object]] = []
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
        rows_for_save.append((entry, result))
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

    if name == "fedsfm":
        _handle_fedsfm(file=file, live=live, dry_run=dry_run)
        return

    _fetch_source_legacy(name, live=live, no_parse=no_parse)


def _handle_fedsfm(
    *,
    file: str | None,
    live: bool,
    dry_run: bool,
) -> None:
    """Handle fedsfm source: --file import or fixture."""
    source_url = "https://fedsfm.ru/documents/terrorists-catalog-portal-act"

    if file:
        _import_fedsfm_file(file, dry_run=dry_run)
        return

    if live:
        typer.echo("Live mode not yet implemented for fedsfm. Use --file.", err=True)
        raise typer.Exit(code=1)

    rows = load_fixture_rows()
    if not rows:
        typer.echo("No fixture data found.", err=True)
        raise typer.Exit(code=1)

    engine = make_engine()
    with session_scope(engine) as session:
        stats = import_rfm_records(session, rows, source_url=source_url)
    typer.echo(
        f"fedsfm: total={stats.total} imported={stats.imported} duplicates={stats.duplicates}"
    )


def _import_fedsfm_file(file: str, *, dry_run: bool) -> None:
    """Import a local RFM file (XML, DBF, ZIP, CSV)."""
    file_path = Path(file)
    if not file_path.exists():
        typer.echo(f"File not found: {file}", err=True)
        raise typer.Exit(code=1)

    file_size = file_path.stat().st_size
    file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
    result = parse_file(file_path)

    _print_fedsfm_preview(file_path.name, file_hash, file_size, result)

    if dry_run:
        typer.echo("\n(режим --dry-run: данные не записаны)")
        return

    if not result.rows:
        typer.echo("Нет записей для импорта.", err=True)
        raise typer.Exit(code=1)

    engine = make_engine()
    with session_scope(engine) as session:
        stats = import_rfm_records(
            session,
            result.rows,
            source="rfm",
            source_url=f"file://{file_path.absolute()}",
        )
    typer.echo(
        f"\nfedsfm: total={stats.total} imported={stats.imported} duplicates={stats.duplicates}"
    )


def _print_fedsfm_preview(filename: str, file_hash: str, file_size: int, result) -> None:
    """Print RFM file preview."""
    typer.echo(f"Файл: {filename}")
    typer.echo(f"SHA-256: {file_hash}")
    typer.echo(f"Размер: {file_size} bytes")
    typer.echo(f"Формат: {result.format_detected}")
    typer.echo(f"Всего записей: {result.total_records}")
    typer.echo(f"Распознано: {result.recognized}")
    typer.echo(f"Не распознано: {result.unrecognized}")

    if result.errors:
        typer.echo(f"Ошибки ({len(result.errors)}):")
        for err in result.errors[:5]:
            typer.echo(f"  - {err}")

    if result.rows:
        typer.echo("\nПримеры первых 5 записей:")
        for i, row in enumerate(result.rows[:5], 1):
            typer.echo(
                f"  {i}. {row.raw_name} | {row.birth_date or '-'} | {row.birth_place or '-'}"
            )


def _fetch_source_legacy(name: str, *, live: bool = False, no_parse: bool = False) -> None:
    monitoring = load_monitoring()

    entry = _registry_entry_or_none(name)
    if entry is not None:
        engine = make_engine()
        with session_scope(engine) as session:
            stats = process_registry_source(
                session,
                entry,
                monitoring,
                live=live,
                fixture_path=str(_fixture_path_for(entry)) if _fixture_path_for(entry) else None,
            )
        typer.echo(_render_registry_stats(stats, entry.id, live=live))
        return

    src = get_source(name)
    if src is None or not src.enabled:
        typer.echo(
            f"Источник «{name}» не найден ни в реестре, ни в config/sources.yaml.",
            err=True,
        )
        raise typer.Exit(code=1)
    engine = make_engine()
    with session_scope(engine) as session:
        stats = process_source(session, src, monitoring, parse_immediately=not no_parse)
    typer.echo(
        f"{name}: fetched={stats.fetched} new={stats.new_documents} "
        f"duplicates={stats.duplicates} parsed={stats.parsed} "
        f"irrelevant={stats.irrelevant} failed={stats.failed}"
    )


def _render_registry_stats(stats, source_id: str, *, live: bool) -> str:
    mode = "live" if live else "fixture"
    return (
        f"{source_id} ({mode}): fetched={stats.fetched} new={stats.new_documents} "
        f"already_exists={stats.duplicates} parsed={stats.parsed} "
        f"irrelevant={stats.irrelevant} failed={stats.failed}"
    )


@app.command(name="fetch-all")
def fetch_all() -> None:
    """Fetch from all enabled legacy sources (config/sources.yaml)."""
    _bootstrap_logging()
    monitoring = load_monitoring()
    engine = make_engine()
    totals = {"fetched": 0, "new": 0, "dup": 0, "parsed": 0, "irr": 0, "fail": 0}
    for src in (s for s in load_sources() if s.enabled):
        try:
            with session_scope(engine) as session:
                stats = process_source(session, src, monitoring)
                totals["fetched"] += stats.fetched
                totals["new"] += stats.new_documents
                totals["dup"] += stats.duplicates
                totals["parsed"] += stats.parsed
                totals["irr"] += stats.irrelevant
                totals["fail"] += stats.failed
        except Exception as exc:
            typer.echo(f"  {src.name}: ERROR {type(exc).__name__}: {exc}", err=True)
            totals["fail"] += 1
    typer.echo(
        f"TOTAL fetched={totals['fetched']} new={totals['new']} "
        f"duplicates={totals['dup']} parsed={totals['parsed']} "
        f"irrelevant={totals['irr']} failed={totals['fail']}"
    )


@app.command()
def parse_pending() -> None:
    """Parse all documents left in 'pending' state."""
    _bootstrap_logging()
    engine = make_engine()
    with session_scope(engine) as session:
        stats = process_pending(session)
    typer.echo(f"parsed={stats.parsed} irrelevant={stats.irrelevant} failed={stats.failed}")


@app.command(name="reprocess-document")
def reprocess_document(
    document_id: Annotated[int, typer.Argument(help="SourceDocument.id")],
) -> None:
    """Drop existing facts for a document and re-parse it."""
    _bootstrap_logging()
    engine = make_engine()
    with session_scope(engine) as session:
        result = reprocess(session, document_id)
    if result is None:
        typer.echo(f"Document {document_id} not found.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Reprocessed document {result}.")


@app.command(name="list-documents")
def list_documents(
    limit: Annotated[int, typer.Option(help="Max rows to print.")] = 50,
) -> None:
    """List documents in a compact table."""
    _bootstrap_logging()
    engine = make_engine()
    factory = make_session_factory(engine)
    with factory() as session:
        docs = repo.list_documents(session, limit=limit)
    typer.echo(f"{'ID':>4}  {'Дата':10}  {'Источник':16} {'Релевантен':10}  Заголовок")
    for d in docs:
        date = d.published_at.strftime("%Y-%m-%d") if d.published_at else "-"
        source = (d.source_id or d.source_name or "-")[:16]
        relevant = "да" if bool(d.relevant) else "нет"
        title = (d.title or "").replace("\n", " ")[:60]
        typer.echo(f"{d.id:>4}  {date:10}  {source:16} {relevant:10}  {title}")


@app.command(name="show-stats")
def show_stats() -> None:
    """Print document/fact counts in a human-readable form."""
    _bootstrap_logging()
    engine = make_engine()
    factory = make_session_factory(engine)
    with factory() as session:
        docs = repo.count_documents(session)
        facts = repo.count_facts(session)
        relevant = repo.count_relevant_documents(session)
    typer.echo(f"Документов: {docs} (релевантных: {relevant})\nИзвлечённых фактов: {facts}")


# ---------------------------------------------------------------------------
# show-document: split into helpers
# ---------------------------------------------------------------------------


def _print_doc_header(doc) -> None:
    """Print document header info."""
    typer.echo(f"Документ: {doc.id}")
    typer.echo(f"Источник: {doc.source_id or doc.source_name or '-'}")
    typer.echo(f"Дата: {doc.published_at.isoformat() if doc.published_at else '-'}")
    typer.echo(f"Заголовок: {doc.title or '-'}")
    typer.echo(f"URL: {doc.canonical_url or doc.url}")
    typer.echo(f"Статус: {doc.parser_status}")
    typer.echo(f"Релевантность: {bool(doc.relevant)}")
    if doc.external_id:
        typer.echo(f"Внешний ID: {doc.external_id}")
    if doc.content_type:
        typer.echo(f"Content-Type: {doc.content_type}")
    typer.echo(f"SHA-256: {doc.content_hash}")


def _print_doc_articles(facts) -> None:
    """Print extracted criminal articles."""
    articles = [f for f in facts if f.field == "criminal_article"]
    typer.echo("\nСтатьи:")
    if not articles:
        typer.echo("- (не найдены)")
        return
    for f in articles:
        val = f.value if isinstance(f.value, dict) else {"article": str(f.value)}
        parts = []
        if val.get("point"):
            parts.append(f"п. «{val['point']}»")
        if val.get("part"):
            parts.append(f"ч. {val['part']}")
        parts.append(f"ст. {val['article']}")
        if val.get("code"):
            parts.append(val["code"])
        typer.echo(f"- {' '.join(parts)}")
        typer.echo(f"  quote: «{f.quote}»")


def _print_doc_dates(facts) -> None:
    """Print extracted dates."""
    dates = [f for f in facts if f.field == "date"]
    typer.echo("\nДаты:")
    if not dates:
        typer.echo("- (не найдены)")
        return
    for f in dates:
        val = f.value if isinstance(f.value, dict) else {"date": str(f.value)}
        typer.echo(f"- {val.get('date', val)}")
        if val.get("type"):
            typer.echo(f"  type: {val['type']}")
        typer.echo(f"  quote: «{f.quote}»")


def _print_doc_people(facts) -> None:
    """Print extracted person names."""
    people = [f for f in facts if f.field == "full_name_original"]
    typer.echo("\nЛюди:")
    if not people:
        typer.echo("- (не найдены)")
        return
    for f in people:
        typer.echo(f"- {f.value}")
        typer.echo(f"  confidence: {f.confidence:.2f}")
        typer.echo(f"  quote: «{f.quote}»")


def _print_doc_content(doc) -> None:
    """Print document text and all facts."""
    typer.echo("\nТекст:")
    typer.echo((doc.text or "")[:1500])

    typer.echo("\nИзвлечённые факты:")
    for f in doc.facts:
        quote = f"«{f.quote}»" if f.quote else "-"
        typer.echo(f"- {f.field} = {f.value}")
        typer.echo(f"  quote: {quote}")
        typer.echo(f"  confidence: {f.confidence:.2f}")


@app.command(name="show-document")
def show_document(
    document_id: Annotated[int, typer.Argument(help="SourceDocument.id")],
) -> None:
    """Show a document and its extracted facts (human-readable)."""
    _bootstrap_logging()
    engine = make_engine()
    factory = make_session_factory(engine)
    with factory() as session:
        doc = repo.get_document(session, document_id)
        if doc is None:
            typer.echo(f"Document {document_id} not found.", err=True)
            raise typer.Exit(code=1)
        _print_doc_header(doc)
        _print_doc_articles(doc.facts)
        _print_doc_dates(doc.facts)
        _print_doc_people(doc.facts)
        _print_doc_content(doc)


# ---------------------------------------------------------------------------
# list-sources + fetch-demo-source
# ---------------------------------------------------------------------------


@app.command(name="list-sources")
def list_sources() -> None:
    """List sources from the Airtable fixture (ID | Name | URL)."""
    fixture = Path("tests/fixtures/airtable/source_registry.json")
    if not fixture.exists():
        typer.echo(f"Fixture not found: {fixture}", err=True)
        raise typer.Exit(code=1)

    with fixture.open() as f:
        rows = json.load(f)

    typer.echo(f"{'ID':4}  {'Название':40}  URL")
    typer.echo("-" * 90)
    for i, row in enumerate(rows, start=1):
        name = row.get("name", "")
        url = row.get("url", "")
        typer.echo(f"{i:<4}  {name:40}  {url}")


def _read_demo_fixture() -> list[dict]:
    """Read the Airtable source fixture."""
    fixture = Path("tests/fixtures/airtable/source_registry.json")
    if not fixture.exists():
        typer.echo(f"Fixture not found: {fixture}", err=True)
        raise typer.Exit(code=1)
    with fixture.open() as f:
        return json.load(f)


def _find_first_reachable(rows: list[dict]) -> tuple[str, str]:
    """Find the first reachable URL and return (url, html)."""
    with HttpClient() as client:
        for row in rows:
            url = row.get("url", "")
            if not url:
                continue
            try:
                resp = client.get(url)
                if resp.health == FetchHealth.ok and resp.text:
                    return url, resp.text
            except Exception:
                continue
    return "", ""


def _extract_page_content(html: str) -> tuple[str, str]:
    """Extract title and body text from HTML."""
    tree = HTMLParser(html)
    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node else "(no title)"

    description = ""
    desc_node = tree.css_first('meta[property="og:description"]')
    if desc_node:
        description = desc_node.attributes.get("content", "") or ""

    body_text = ""
    for selector in [
        "div.tgme_widget_message_text",
        "div.tgme_page_description",
        "article",
        "main",
    ]:
        nodes = tree.css(selector)
        if nodes:
            body_text = "\n".join(n.text(strip=True) for n in nodes if n.text(strip=True))
            break

    if not body_text:
        body_text = description or tree.body.text(strip=True)[:2000] if tree.body else ""

    return title, body_text


@app.command(name="fetch-demo-source")
def fetch_demo_source() -> None:
    """Fetch one demo page from the first working source and extract title + text."""
    rows = _read_demo_fixture()
    if not rows:
        typer.echo("No sources in fixture.", err=True)
        raise typer.Exit(code=1)

    target_url, html = _find_first_reachable(rows)
    if not target_url:
        typer.echo("No reachable source found.", err=True)
        raise typer.Exit(code=1)

    out_dir = Path("tests/fixtures/demo")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "demo_page.html"
    out_file.write_text(html, encoding="utf-8")
    typer.echo(f"Saved fixture: {out_file}")

    title, body_text = _extract_page_content(html)
    typer.echo(f"\nURL: {target_url}")
    typer.echo(f"Title: {title}")
    typer.echo(f"\nText:\n{body_text[:1500]}")


# ---------------------------------------------------------------------------
# Rosfinmonitoring (RFM) person records
# ---------------------------------------------------------------------------


@app.command(name="list-person-records")
def list_person_records_cmd(
    source: Annotated[str, typer.Option("--source", help="Filter by source (e.g. rfm).")] = "rfm",
    limit: Annotated[int, typer.Option(help="Max rows to print.")] = 50,
) -> None:
    """List person records from external registries."""
    _bootstrap_logging()
    engine = make_engine()
    factory = make_session_factory(engine)
    with factory() as session:
        records = repo.list_person_records(session, source=source, limit=limit)
        total = repo.count_person_records(session, source=source)

    typer.echo(f"Всего записей ({source}): {total}")
    typer.echo(f"{'ID':>4}  {'ФИО':40}  {'Дата рожд.':12}  {'Основание':30}")
    typer.echo("-" * 100)
    for r in records:
        name = r.raw_name[:40]
        birth = r.birth_date or "-"
        cat = (r.category or "-")[:30]
        typer.echo(f"{r.id:>4}  {name:40}  {birth:12}  {cat:30}")


@app.command(name="show-person-record")
def show_person_record_cmd(
    record_id: Annotated[int, typer.Argument(help="PersonRecord.id")],
) -> None:
    """Show a person record in detail."""
    _bootstrap_logging()
    engine = make_engine()
    factory = make_session_factory(engine)
    with factory() as session:
        rec = repo.get_person_record(session, record_id)
        if rec is None:
            typer.echo(f"Record {record_id} not found.", err=True)
            raise typer.Exit(code=1)

        typer.echo(f"ID: {rec.id}")
        typer.echo(f"Источник: {rec.source}")
        typer.echo(f"ФИО (raw): {rec.raw_name}")
        typer.echo(f"ФИО (search): {rec.search_name}")
        typer.echo(f"ФИО (normalized): {rec.normalized_name}")
        typer.echo(f"Confidence нормализации: {rec.normalization_confidence:.2f}")
        typer.echo(f"Метод нормализации: {rec.normalization_method}")
        typer.echo(f"Дата рождения: {rec.birth_date or '-'}")
        typer.echo(f"Место рождения: {rec.birth_place or '-'}")
        typer.echo(f"Основание: {rec.category or '-'}")
        typer.echo(f"Номер записи: {rec.source_ref or '-'}")
        typer.echo(f"Дата включения: {rec.added_date or '-'}")
        typer.echo(f"URL источника: {rec.source_url or '-'}")
        typer.echo(f"Загружено: {rec.fetched_at.isoformat() if rec.fetched_at else '-'}")


def _safe_url(url: str) -> str:
    if "://" in url and "@" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


# ---------------------------------------------------------------------------
# Person matching: split into helpers
# ---------------------------------------------------------------------------


@app.command(name="generate-matches")
def generate_matches_cmd() -> None:
    """Generate match candidates between person facts and registry records."""
    _bootstrap_logging()
    engine = make_engine()
    with session_scope(engine) as session:
        stats = generate_matches(session)

    typer.echo(f"Документов обработано: {stats.get('documents_processed', '-')}")
    typer.echo(f"Фактов person: {stats['facts_person']}")
    typer.echo(f"Кандидатов создано: {stats['candidates_created']}")
    typer.echo(f"Уже существовало: {stats['already_existed']}")
    typer.echo(f"Без кандидатов: {stats['no_candidates']}")
    typer.echo(f"Ошибок: {stats['errors']}")


@app.command(name="list-matches")
def list_matches_cmd(
    status: Annotated[
        str | None, typer.Option("--status", help="Filter by status (pending/confirmed/rejected).")
    ] = None,
    limit: Annotated[int, typer.Option(help="Max rows to print.")] = 50,
) -> None:
    """List match candidates."""
    _bootstrap_logging()
    engine = make_engine()
    factory = make_session_factory(engine)
    with factory() as session:
        candidates = repo.list_match_candidates(session, status=status, limit=limit)
        total = repo.count_match_candidates(session, status=status)

        typer.echo(f"Всего кандидатов{f' (status={status})' if status else ''}: {total}")
        typer.echo(
            f"{'ID':>4}  {'Документ':>8}  {'Имя в тексте':30}"
            f"  {'Запись':>6}  {'Score':>5}  {'Статус':10}"
        )
        typer.echo("-" * 80)
        for c in candidates:
            _print_match_row(c)


def _print_match_row(c) -> None:
    """Print one match candidate row."""
    fact = c.extracted_fact
    record = c.person_record
    doc_id = fact.document_id if fact else "-"
    name_raw = (
        (fact.value if isinstance(fact.value, str) else str(fact.value))[:30] if fact else "-"
    )
    rec_id = record.id if record else "-"
    typer.echo(
        f"{c.id:>4}  {str(doc_id):>8}  {name_raw:30}"
        f"  {str(rec_id):>6}  {c.score:>5.2f}  {c.status:10}"
    )


def _print_match_header(c) -> None:
    """Print match candidate header."""
    typer.echo(f"ID: {c.id}")
    typer.echo(f"Статус: {c.status}")
    typer.echo(f"Score: {c.score:.2f}")
    typer.echo(f"Algorithm: {c.algorithm_version}")
    typer.echo(f"Создан: {c.created_at.isoformat() if c.created_at else '-'}")
    if c.reviewed_at:
        typer.echo(f"Рассмотрен: {c.reviewed_at.isoformat()}")
    if c.review_comment:
        typer.echo(f"Комментарий: {c.review_comment}")


def _print_match_document(fact) -> None:
    """Print match document section."""
    typer.echo("\n--- Документ ---")
    if not fact:
        return
    typer.echo(f"Fact ID: {fact.id}")
    typer.echo(f"Document ID: {fact.document_id}")
    typer.echo(f"Имя в тексте: {fact.value}")
    if fact.quote:
        typer.echo(f"Цитата: «{fact.quote}»")


def _print_match_record(record) -> None:
    """Print match PersonRecord section."""
    typer.echo("\n--- Запись ---")
    if not record:
        return
    typer.echo(f"Record ID: {record.id}")
    typer.echo(f"ФИО (raw): {record.raw_name}")
    typer.echo(f"ФИО (normalized): {record.normalized_name}")
    typer.echo(f"Дата рождения: {record.birth_date or '-'}")
    typer.echo(f"Место рождения: {record.birth_place or '-'}")


def _print_match_score(c) -> None:
    """Print match score breakdown."""
    typer.echo("\n--- Оценка ---")
    typer.echo(f"Имя: {c.name_score:.2f}")
    typer.echo(f"Дата рождения: {c.birth_date_score:.2f}")
    typer.echo(f"Место рождения: {c.birthplace_score:.2f}")


def _print_match_reasons(reasons_json: str) -> None:
    """Print match reasons."""
    typer.echo("\nПричины:")
    for r in json.loads(reasons_json):
        impact = r.get("impact", 0)
        typer.echo(f"  +{impact:.2f}  {r['rule']}")
        if "document_value" in r:
            typer.echo(f"         док: {r['document_value']}")
        if "record_value" in r:
            typer.echo(f"         зап: {r['record_value']}")


def _print_match_conflicts(conflicts_json: str) -> None:
    """Print match conflicts."""
    typer.echo("\nКонфликты:")
    for r in json.loads(conflicts_json):
        typer.echo(f"  {r['rule']}")
        if "document_value" in r:
            typer.echo(f"    док: {r['document_value']}")
        if "record_value" in r:
            typer.echo(f"    зап: {r['record_value']}")


@app.command(name="show-match")
def show_match_cmd(
    candidate_id: Annotated[int, typer.Argument(help="MatchCandidate.id")],
) -> None:
    """Show detailed match candidate information."""
    _bootstrap_logging()
    engine = make_engine()
    factory = make_session_factory(engine)
    with factory() as session:
        c = repo.get_match_candidate(session, candidate_id)
        if c is None:
            typer.echo(f"Candidate {candidate_id} not found.", err=True)
            raise typer.Exit(code=1)

        _print_match_header(c)
        _print_match_document(c.extracted_fact)
        _print_match_record(c.person_record)
        _print_match_score(c)

        if c.reasons_json:
            _print_match_reasons(c.reasons_json)
        if c.conflicts_json:
            _print_match_conflicts(c.conflicts_json)


def _default_operator() -> str:
    """Best-effort identity for the audit trail (spec §17)."""
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - defensive, no login name available
        return "unknown"


_OPERATOR_OPTION = typer.Option("--operator", help="Who is making this decision (audit trail).")


@app.command(name="confirm-match")
def confirm_match_cmd(
    candidate_id: Annotated[int, typer.Argument(help="MatchCandidate.id")],
    comment: Annotated[str, typer.Option("--comment", help="Review comment.")] = "",
    operator: Annotated[str, _OPERATOR_OPTION] = "",
) -> None:
    """Confirm a match candidate."""
    _bootstrap_logging()
    engine = make_engine()
    with session_scope(engine) as session, correlation_scope() as cid:
        c = repo.update_match_status(
            session,
            candidate_id,
            "confirmed",
            comment or None,
            actor=operator or _default_operator(),
            correlation_id=cid,
        )
        if c is None:
            typer.echo(f"Candidate {candidate_id} not found.", err=True)
            raise typer.Exit(code=1)
    typer.echo(f"Match {candidate_id} confirmed.")


@app.command(name="reject-match")
def reject_match_cmd(
    candidate_id: Annotated[int, typer.Argument(help="MatchCandidate.id")],
    comment: Annotated[str, typer.Option("--comment", help="Review comment.")] = "",
    operator: Annotated[str, _OPERATOR_OPTION] = "",
) -> None:
    """Reject a match candidate."""
    _bootstrap_logging()
    engine = make_engine()
    with session_scope(engine) as session, correlation_scope() as cid:
        c = repo.update_match_status(
            session,
            candidate_id,
            "rejected",
            comment or None,
            actor=operator or _default_operator(),
            correlation_id=cid,
        )
        if c is None:
            typer.echo(f"Candidate {candidate_id} not found.", err=True)
            raise typer.Exit(code=1)
    typer.echo(f"Match {candidate_id} rejected.")


@app.command(name="list-review-items")
def list_review_items_cmd(
    status: Annotated[
        str | None, typer.Option("--status", help="Filter by status (pending/resolved/dismissed).")
    ] = None,
    item_type: Annotated[str | None, typer.Option("--type", help="Filter by item_type.")] = None,
    limit: Annotated[int, typer.Option(help="Max rows to print.")] = 50,
) -> None:
    """List operator review items (parser failures, etc.)."""
    _bootstrap_logging()
    engine = make_engine()
    factory = make_session_factory(engine)
    with factory() as session:
        items = repo.list_review_items(session, status=status, item_type=item_type, limit=limit)
        total = repo.count_review_items(session, status=status)

        typer.echo(f"Всего review items{f' (status={status})' if status else ''}: {total}")
        typer.echo(
            f"{'ID':>4}  {'Тип':16}  {'Приоритет':9}  {'Документ':>8}  {'Статус':10}  {'Создан'}"
        )
        typer.echo("-" * 80)
        for item in items:
            doc_id = "-" if item.document_id is None else str(item.document_id)
            typer.echo(
                f"{item.id:>4}  {item.item_type:16}  {item.priority:9}"
                f"  {doc_id:>8}  {item.status:10}"
                f"  {item.created_at.isoformat() if item.created_at else '-'}"
            )


@app.command(name="resolve-review-item")
def resolve_review_item_cmd(
    item_id: Annotated[int, typer.Argument(help="ReviewItem.id")],
    comment: Annotated[str, typer.Option("--comment", help="Resolution comment.")] = "",
    dismiss: Annotated[
        bool, typer.Option("--dismiss", help="Mark as dismissed instead of resolved.")
    ] = False,
    operator: Annotated[str, _OPERATOR_OPTION] = "",
) -> None:
    """Mark a review item as resolved (or dismissed)."""
    _bootstrap_logging()
    engine = make_engine()
    with session_scope(engine) as session, correlation_scope() as cid:
        actor = operator or _default_operator()
        item = repo.resolve_review_item(
            session,
            item_id,
            status="dismissed" if dismiss else "resolved",
            resolved_by=actor,
            comment=comment or None,
            correlation_id=cid,
        )
        if item is None:
            typer.echo(f"Review item {item_id} not found.", err=True)
            raise typer.Exit(code=1)
    typer.echo(f"Review item {item_id} → {item.status}.")


__all__ = ["app", "ImportPreview"]


if __name__ == "__main__":
    app()

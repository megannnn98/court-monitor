"""court-monitor CLI entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
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
from court_monitor.observability import configure_logging, get_logger
from court_monitor.services import (
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


@app.command()
def doctor() -> None:
    """Sanity-check the environment: config, DB connectivity, dependencies."""
    _bootstrap_logging()
    typer.echo(f"court-monitor {__version__}")
    typer.echo(f"python: {sys.version.split()[0]}")
    typer.echo(f"database_url: {_safe_url(settings.database_url)}")
    typer.echo(f"airtable_mode: {settings.airtable_mode}")
    typer.echo(f"llm_mode: {settings.llm_mode}")

    problems: list[str] = []
    try:
        monitoring = load_monitoring()
        typer.echo(
            f"monitoring: {len(monitoring.article_set())} articles, "
            f"{len(monitoring.keyword_set())} keywords"
        )
    except Exception as exc:  # pragma: no cover - reported in CLI
        problems.append(f"monitoring config error: {exc}")

    try:
        registry = load_registry()
        typer.echo(f"registry: {len(registry)} sources ({DEFAULT_REGISTRY_PATH})")
    except Exception as exc:  # pragma: no cover
        problems.append(f"registry error: {exc}")

    try:
        engine = make_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        typer.echo("db: connection OK")
    except Exception as exc:
        problems.append(f"db connection failed: {exc}")

    if problems:
        typer.echo("\nProblems:")
        for p in problems:
            typer.echo(f"  - {p}")
        raise typer.Exit(code=1)
    typer.echo("\nAll checks passed.")


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
    live: Annotated[
        bool, typer.Option("--live", help="Fetch the real source over HTTP (default: fixture).")
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Cap the number of materials fetched."),
    ] = None,
) -> None:
    """Fetch documents from one source and ingest them."""
    _bootstrap_logging()
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
                limit=limit,
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
        stats = process_source(session, src, monitoring)
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
    with session_scope(engine) as session:
        for src in (s for s in load_sources() if s.enabled):
            stats = process_source(session, src, monitoring)
            totals["fetched"] += stats.fetched
            totals["new"] += stats.new_documents
            totals["dup"] += stats.duplicates
            totals["parsed"] += stats.parsed
            totals["irr"] += stats.irrelevant
            totals["fail"] += stats.failed
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

        articles = [f for f in doc.facts if f.field == "criminal_article"]
        people = [f for f in doc.facts if f.field == "full_name_original"]
        typer.echo("\nСтатьи:")
        if articles:
            for f in articles:
                typer.echo(f"- {f.value}")
        else:
            typer.echo("- (не найдены)")

        typer.echo("\nЛюди:")
        if people:
            for f in people:
                typer.echo(f"- {f.value}")
        else:
            typer.echo("- (не найдены)")

        typer.echo("\nТекст:")
        typer.echo((doc.text or "")[:1500])

        typer.echo("\nИзвлечённые факты:")
        for f in doc.facts:
            quote = f"«{f.quote}»" if f.quote else "-"
            typer.echo(f"- {f.field} = {f.value}")
            typer.echo(f"  quote: {quote}")
            typer.echo(f"  confidence: {f.confidence:.2f}")


@app.command(name="list-sources")
def list_sources() -> None:
    """List sources from the Airtable fixture (ID | Name | URL)."""
    fixture = Path("tests/fixtures/airtable/source_registry.json")
    if not fixture.exists():
        typer.echo(f"Fixture not found: {fixture}", err=True)
        raise typer.Exit(code=1)

    import json

    with fixture.open() as f:
        rows = json.load(f)

    typer.echo(f"{'ID':4}  {'Название':40}  URL")
    typer.echo("-" * 90)
    for i, row in enumerate(rows, start=1):
        name = row.get("name", "")
        url = row.get("url", "")
        typer.echo(f"{i:<4}  {name:40}  {url}")


@app.command(name="fetch-demo-source")
def fetch_demo_source() -> None:
    """Fetch one demo page from the first working source and extract title + text."""
    fixture = Path("tests/fixtures/airtable/source_registry.json")
    if not fixture.exists():
        typer.echo(f"Fixture not found: {fixture}", err=True)
        raise typer.Exit(code=1)

    import json

    import httpx
    from selectolax.parser import HTMLParser

    with fixture.open() as f:
        rows = json.load(f)

    if not rows:
        typer.echo("No sources in fixture.", err=True)
        raise typer.Exit(code=1)

    # Find first reachable URL
    target_url = None
    for row in rows:
        url = row.get("url", "")
        if not url:
            continue
        try:
            resp = httpx.get(url, timeout=10, follow_redirects=True)
            if resp.status_code == 200:
                target_url = url
                break
        except Exception:
            continue

    if target_url is None:
        typer.echo("No reachable source found.", err=True)
        raise typer.Exit(code=1)

    # Fetch and save fixture
    resp = httpx.get(target_url, timeout=10, follow_redirects=True)
    html = resp.text

    out_dir = Path("tests/fixtures/demo")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "demo_page.html"
    out_file.write_text(html, encoding="utf-8")
    typer.echo(f"Saved fixture: {out_file}")

    # Extract title and text
    tree = HTMLParser(html)
    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node else "(no title)"

    # Telegram preview pages: extract description and content
    description = ""
    desc_node = tree.css_first('meta[property="og:description"]')
    if desc_node:
        description = desc_node.attributes.get("content", "")

    body_text = ""
    # Try common content selectors
    for selector in ["div.tgme_widget_message_text", "div.tgme_page_description", "article", "main"]:
        nodes = tree.css(selector)
        if nodes:
            body_text = "\n".join(n.text(strip=True) for n in nodes if n.text(strip=True))
            break

    if not body_text:
        body_text = description or tree.body.text(strip=True)[:2000] if tree.body else ""

    typer.echo(f"\nURL: {target_url}")
    typer.echo(f"Title: {title}")
    typer.echo(f"\nText:\n{body_text[:1500]}")


def _safe_url(url: str) -> str:
    if "://" in url and "@" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


__all__ = ["app", "ImportPreview"]


if __name__ == "__main__":
    app()

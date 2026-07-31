"""Commands over stored documents: listing, inspection, re-parsing."""

from __future__ import annotations

from typing import Annotated

import typer

from court_monitor.cli._shared import bootstrap_logging, maybe_dry_run_session
from court_monitor.config.settings import settings
from court_monitor.domain.models import PERSON_NAME_FIELD
from court_monitor.observability import configure_logging
from court_monitor.services import (
    ReprocessWouldDiscardDecisions,
    process_pending,
    reprocess,
    reprocess_all,
)
from court_monitor.storage import repository as repo
from court_monitor.storage.db import make_engine, make_session_factory, session_scope


def parse_pending() -> None:
    """Parse all documents left in 'pending' state."""
    bootstrap_logging()
    engine = make_engine()
    with session_scope(engine) as session:
        stats = process_pending(session)
    typer.echo(f"parsed={stats.parsed} irrelevant={stats.irrelevant} failed={stats.failed}")


def reprocess_document(
    document_id: Annotated[int, typer.Argument(help="SourceDocument.id")],
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-parse even if it discards reviewed match decisions."),
    ] = False,
) -> None:
    """Drop existing facts for a document and re-parse it."""
    bootstrap_logging()
    engine = make_engine()
    try:
        with session_scope(engine) as session:
            result = reprocess(session, document_id, force=force)
    except ReprocessWouldDiscardDecisions as exc:
        typer.secho(str(exc), fg=typer.colors.RED, bold=True, err=True)
        typer.echo(
            "Решения оператора будут потеряны безвозвратно. "
            "Если это действительно нужно — повторите с --force.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    if result is None:
        typer.echo(f"Document {document_id} not found.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Reprocessed document {result}.")


def reprocess_all_documents(
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-parse even if it discards reviewed match decisions."),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option(help="Only re-parse this many documents (oldest first)."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would change; roll back afterwards."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show per-document JSON logs."),
    ] = False,
) -> None:
    """Re-parse every stored document with the current extractors.

    Like ``run-all``, the per-document logs are suppressed by default: 379 JSON
    lines bury the one summary the operator actually needs.
    """
    configure_logging(settings.log_level if verbose else "ERROR")
    engine = make_engine()

    def _progress(done: int, total: int) -> None:
        if done % 25 == 0 or done == total:
            typer.echo(f"  {done}/{total}", err=True)

    try:
        with maybe_dry_run_session(engine, dry_run=dry_run) as session:
            stats = reprocess_all(session, force=force, limit=limit, on_progress=_progress)
    except ReprocessWouldDiscardDecisions as exc:
        typer.secho(str(exc), fg=typer.colors.RED, bold=True, err=True)
        typer.echo(
            "Решения оператора будут потеряны безвозвратно. "
            "Если это действительно нужно — повторите с --force.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"Переразобрано документов: {stats.documents} "
        f"(parsed={stats.parsed} irrelevant={stats.irrelevant} failed={stats.failed})"
    )
    typer.echo(f"Фактов: {stats.facts_before} → {stats.facts_after} ({stats.facts_delta:+d})")
    if dry_run:
        typer.secho("(режим --dry-run: изменения откачены)", fg=typer.colors.YELLOW)


def list_documents(
    limit: Annotated[int, typer.Option(help="Max rows to print.")] = 50,
) -> None:
    """List documents in a compact table."""
    bootstrap_logging()
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


def show_stats() -> None:
    """Print document/fact counts in a human-readable form."""
    bootstrap_logging()
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
    people = [f for f in facts if f.field == PERSON_NAME_FIELD]
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


def show_document(
    document_id: Annotated[int, typer.Argument(help="SourceDocument.id")],
) -> None:
    """Show a document and its extracted facts (human-readable)."""
    bootstrap_logging()
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


def register(app: typer.Typer) -> None:
    app.command()(parse_pending)
    app.command(name="reprocess-document")(reprocess_document)
    app.command(name="reprocess-all")(reprocess_all_documents)
    app.command(name="list-documents")(list_documents)
    app.command(name="show-stats")(show_stats)
    app.command(name="show-document")(show_document)

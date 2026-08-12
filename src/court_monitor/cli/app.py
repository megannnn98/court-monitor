"""court-monitor CLI entrypoint."""

from __future__ import annotations

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
from court_monitor.domain.models import SourceBackend
from court_monitor.matching.candidates import generate_matches
from court_monitor.observability import configure_logging, get_logger
from court_monitor.services import (
    SourceStats,
    process_registry_source,
    process_source,
)
from court_monitor.services.work import plan_all_work, telegram_fixture_path
from court_monitor.sources.airtable_registry import (
    DEFAULT_REGISTRY_VIEW_URL,
    parse_airtable_shared_view,
    parse_registry_csv,
    render_shared_view,
)
from court_monitor.sources.probe import probe_source
from court_monitor.storage import repository as repo
from court_monitor.storage.db import make_engine, make_session_factory, session_scope

from ._shared import (
    bootstrap_logging,
    db_display_path,
    maybe_dry_run_session,
    require_current_schema,
)
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
        # Needs the negative form spelled out: with only "--dry-run" declared,
        # Typer offers no way to turn the default off and save_registry below
        # becomes unreachable.
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Preview only; do not write (default behaviour).",
        ),
    ] = True,
    registry_path: Annotated[
        str, typer.Option(help="Local registry YAML to read/write.")
    ] = DEFAULT_REGISTRY_PATH,
) -> None:
    """Import the source list from Airtable or a CSV export (preview by default)."""
    bootstrap_logging()
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
    bootstrap_logging()
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
    replace: Annotated[
        bool,
        typer.Option(
            "--replace",
            help="Registry sources only: drop the stored list before importing.",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="With --replace: proceed even if it discards decisions."),
    ] = False,
    full_rescan: Annotated[
        bool,
        typer.Option(
            "--full-rescan",
            help="Sudrf sources only: re-fetch all documents, ignoring incremental state.",
        ),
    ] = False,
) -> None:
    """Fetch data from a source (RFM, sudrf, etc.)."""
    bootstrap_logging()
    # Checked before the fetch: the RFM list is a 4 MB download parsed into 21k
    # rows, and writing them is what first touches a column a pending migration
    # would have added.
    require_current_schema()

    if name == "fedsfm":
        _handle_fedsfm(file=file, live=live, dry_run=dry_run, replace=replace, force=force)
        return

    if replace:
        typer.echo("--replace применим только к источникам-перечням (fedsfm).", err=True)
        raise typer.Exit(code=1)

    _fetch_source_legacy(
        name,
        live=live,
        no_parse=no_parse,
        limit=limit,
        dry_run=dry_run,
        full_rescan=full_rescan,
    )


def _fetch_source_legacy(
    name: str,
    *,
    live: bool = False,
    no_parse: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
    full_rescan: bool = False,
) -> None:
    monitoring = load_monitoring()

    entry = _registry_entry_or_none(name)
    if entry is not None:
        fixture_path = telegram_fixture_path(entry)
        engine = make_engine()
        with maybe_dry_run_session(engine, dry_run=dry_run) as session:
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
    with maybe_dry_run_session(engine, dry_run=dry_run) as session:
        stats = process_source(
            session,
            src,
            monitoring,
            parse_immediately=not no_parse,
            limit=limit,
            full_rescan=full_rescan,
            live=live,
        )
    typer.echo(
        f"{name}: fetched={stats.fetched} new={stats.new_documents} "
        f"duplicates={stats.duplicates} parsed={stats.parsed} "
        f"irrelevant={stats.irrelevant} failed={stats.failed} blocked={stats.blocked}"
    )
    if dry_run:
        typer.echo("\n(режим --dry-run: данные не записаны)")


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
    bootstrap_logging()
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
        f"База данных: {db_display_path(settings.database_url)}",
        fg=typer.colors.WHITE,
        bold=True,
    )

    for index, group in enumerate(plan_all_work(monitoring, live=live)):
        if index:
            typer.echo()
        typer.secho(f"=== {group.title} ===", fg=typer.colors.CYAN, bold=True)
        if not group.items:
            typer.secho(f"  ({group.empty_note})", dim=True)
            continue
        for item in group.items:
            typer.secho(f"  → {item.label}...", dim=True)
            try:
                # One session per source: a source that blows up must not roll
                # back what the previous ones already wrote.
                with session_scope(engine) as session:
                    stats = item.run(session)
                    totals.accumulate(stats)
                note = " (нет сохранённой fixture — пропущено)" if item.fixture_missing else ""
                typer.secho(
                    f"  ✓ {item.label}: {stats.summary()}{note}",
                    fg=_stats_color(stats, skipped=item.fixture_missing),
                )
            except Exception as exc:
                typer.secho(
                    f"  ✗ {item.label}: ОШИБКА {type(exc).__name__}: {exc}",
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
    matches_color = typer.colors.RED if match_stats.errors else typer.colors.GREEN
    typer.secho(
        f"  создано={match_stats.candidates_created} "
        f"уже_было={match_stats.already_existed} "
        f"без_кандидата={match_stats.no_candidates} "
        f"ошибок={match_stats.errors}",
        fg=matches_color,
        bold=True,
    )

    _run_court_stage(engine, live=live)

    typer.secho(
        f"\nДанные сохранены в БД: {db_display_path(settings.database_url)}",
        fg=typer.colors.WHITE,
        bold=True,
    )


def _run_court_stage(engine, *, live: bool) -> None:
    """Court case monitoring stage inside ``run-all`` (task §16).

    Iterates configured ``sudrf`` sources and runs the case-matching pipeline
    for pending press documents. The ``live`` flag is forwarded to the
    orchestrator so fixture vs. HTTP semantics stay consistent with the rest
    of ``run-all``. Errors from individual courts are captured per-source —
    they never roll up into the generic fetch counters.
    """
    from court_monitor.config import loader as cfg_loader  # noqa: PLC0415
    from court_monitor.domain.models import SourceType  # noqa: PLC0415
    from court_monitor.services.court_orchestrator import (  # noqa: PLC0415
        process_all_pending_court_documents,
    )

    typer.secho("\n=== Судебные дела ===", fg=typer.colors.CYAN, bold=True)

    courts = [s for s in cfg_loader.load_sources() if s.type == SourceType.sudrf]
    if not courts:
        typer.secho("  (нет настроенных судов)", dim=True)
        return

    for court in courts:
        try:
            with session_scope(engine) as session:
                stats = process_all_pending_court_documents(
                    session, court_name=court.name, force_live=live
                )
            typer.secho(f"  {court.name}:", bold=True)
            typer.secho(f"    processed={stats.processed}")
            typer.secho(f"    review={stats.review_created}")
            typer.secho(f"    no_match={stats.no_match}")
            typer.secho(f"    temporary_failure={stats.temporary_failures}")
            typer.secho(f"    failed={stats.failed}")
        except Exception as exc:
            typer.secho(
                f"  ✗ {court.name}: ОШИБКА {type(exc).__name__}: {exc}",
                fg=typer.colors.RED,
                bold=True,
                err=True,
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
    bootstrap_logging()
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


@app.command(name="list-case-matches")
def list_case_matches(
    status: Annotated[
        str | None,
        typer.Option(
            "--status", help="Filter: pending (default), confirmed, rejected, insufficient."
        ),
    ] = "pending",
    limit: Annotated[int, typer.Option(help="Max rows to print.")] = 50,
) -> None:
    """List case match candidates awaiting operator review (task §12)."""
    bootstrap_logging()
    from sqlalchemy import select  # noqa: PLC0415

    from court_monitor.services.case_match_service import (  # noqa: PLC0415
        list_pending_case_match_candidates,
    )
    from court_monitor.storage.db import make_engine, session_scope  # noqa: PLC0415
    from court_monitor.storage.orm import Case, CaseMatchCandidate  # noqa: PLC0415

    engine = make_engine()
    with session_scope(engine) as session:
        if status == "pending":
            candidates = list_pending_case_match_candidates(session, limit=limit)
        else:
            stmt = (
                select(CaseMatchCandidate)
                .where(CaseMatchCandidate.status == status)
                .order_by(CaseMatchCandidate.score.desc())
                .limit(limit)
            )
            candidates = list(session.execute(stmt).scalars().all())

        typer.echo(f"Всего кандидатов: {len(candidates)}")
        typer.echo(f"{'id':>6}  {'score':>5}  {'status':10}  {'case':25}  {'court':20}  press_doc")
        typer.echo("-" * 100)
        for c in candidates:
            case = session.get(Case, c.case_id)
            case_str = (case.case_number or "?") if case else "?"
            court_str = (case.court or "?") if case else "?"
            typer.echo(
                f"{c.id:>6}  "
                f"{c.score:>5.2f}  "
                f"{c.status[:10]:10}  "
                f"{case_str[:25]:25}  "
                f"{court_str[:20]:20}  "
                f"{c.source_document_id}"
            )


@app.command(name="show-case-match")
def show_case_match(
    candidate_id: Annotated[int, typer.Argument(help="CaseMatchCandidate id.")],
) -> None:
    """Show a single candidate with the match signals (task §12)."""
    bootstrap_logging()
    import json  # noqa: PLC0415

    from court_monitor.services.case_match_service import (  # noqa: PLC0415
        get_case_match_candidate,
    )
    from court_monitor.storage.db import make_engine, session_scope  # noqa: PLC0415
    from court_monitor.storage.orm import Case, SourceDocument  # noqa: PLC0415

    engine = make_engine()
    with session_scope(engine) as session:
        candidate = get_case_match_candidate(session, candidate_id)
        if candidate is None:
            typer.echo(f"Кандидат id={candidate_id} не найден.", err=True)
            raise typer.Exit(code=1)

        case = session.get(Case, candidate.case_id)
        press_doc = session.get(SourceDocument, candidate.source_document_id)
        signals = json.loads(candidate.signals_json) if candidate.signals_json else []

        typer.secho("\nPRESS RELEASE", fg=typer.colors.CYAN, bold=True)
        if press_doc:
            typer.echo(f"  title:       {press_doc.title or '-'}")
            typer.echo(f"  url:         {press_doc.url}")
            typer.echo(f"  published:   {press_doc.published_at or '-'}")
        else:
            typer.echo("  (пресс-релиз не найден)")

        typer.secho("\nCASE", fg=typer.colors.CYAN, bold=True)
        if case:
            typer.echo(f"  case number: {case.case_number}")
            typer.echo(f"  court:       {case.court}")
            typer.echo(f"  case url:    {case.source_url or '-'}")
            typer.echo(f"  received:    {case.received_at or '-'}")
            typer.echo(f"  decision_at: {case.decision_at or '-'}")
        else:
            typer.echo("  (дело не найдено)")

        typer.secho("\nMATCH EXPLANATION", fg=typer.colors.CYAN, bold=True)
        typer.echo(f"  score:       {candidate.score:.2f}")
        typer.echo(f"  status:      {candidate.status}")
        typer.echo(f"  signals:     {len(signals)}")
        for s in signals:
            marker = "✓" if s.get("weight", 0) > 0 else "⚠"
            typer.echo(
                f"    {marker} {s.get('signal_type'):22} "
                f"criteria={s.get('criteria_value')}  case={s.get('case_value')}  "
                f"w={s.get('weight')}"
            )


@app.command(name="confirm-case-match")
def confirm_case_match(
    candidate_id: Annotated[int, typer.Argument(help="CaseMatchCandidate id.")],
    comment: Annotated[str, typer.Option("--comment", help="Operator comment.")],
    operator: Annotated[str, typer.Option("--operator", help="Operator identifier.")] = "cli",
) -> None:
    """Confirm a case match candidate (task §12)."""
    bootstrap_logging()
    from court_monitor.services.case_match_service import (  # noqa: PLC0415
        CaseMatchDecision,
        review_case_match_candidate,
    )
    from court_monitor.storage.db import make_engine, session_scope  # noqa: PLC0415

    engine = make_engine()
    with session_scope(engine) as session:
        try:
            candidate = review_case_match_candidate(
                session,
                candidate_id=candidate_id,
                decision=CaseMatchDecision.CONFIRM,
                actor=operator,
                comment=comment,
            )
        except (ValueError, LookupError) as exc:
            typer.echo(f"Ошибка: {exc}", err=True)
            raise typer.Exit(code=2) from None
        session.commit()
    typer.echo(f"Кандидат {candidate.id}: status → {candidate.status}")


@app.command(name="reject-case-match")
def reject_case_match(
    candidate_id: Annotated[int, typer.Argument(help="CaseMatchCandidate id.")],
    comment: Annotated[str, typer.Option("--comment", help="Operator comment.")],
    operator: Annotated[str, typer.Option("--operator", help="Operator identifier.")] = "cli",
) -> None:
    """Reject a case match candidate (task §12)."""
    bootstrap_logging()
    from court_monitor.services.case_match_service import (  # noqa: PLC0415
        CaseMatchDecision,
        review_case_match_candidate,
    )
    from court_monitor.storage.db import make_engine, session_scope  # noqa: PLC0415

    engine = make_engine()
    with session_scope(engine) as session:
        try:
            candidate = review_case_match_candidate(
                session,
                candidate_id=candidate_id,
                decision=CaseMatchDecision.REJECT,
                actor=operator,
                comment=comment,
            )
        except (ValueError, LookupError) as exc:
            typer.echo(f"Ошибка: {exc}", err=True)
            raise typer.Exit(code=2) from None
        session.commit()
    typer.echo(f"Кандидат {candidate.id}: status → {candidate.status}")


@app.command(name="find-case")
def find_case(  # noqa: PLR0917
    court: Annotated[str, typer.Option("--court", help="Court identifier (e.g. 2zovs).")] = "2zovs",
    article: Annotated[
        str | None, typer.Option("--article", help="Article number (e.g. 205.1).")
    ] = None,
    result_date: Annotated[
        str | None, typer.Option("--result-date", help="Decision/result date DD.MM.YYYY.")
    ] = None,
    event_date: Annotated[
        str | None, typer.Option("--event-date", help="Event date DD.MM.YYYY (any event).")
    ] = None,
    date: Annotated[
        str | None,
        typer.Option(
            "--date",
            help="Deprecated alias for --result-date (kept for back-compat).",
            hidden=True,
        ),
    ] = None,
    case_number: Annotated[str | None, typer.Option("--case-number", help="Case number.")] = None,
    person: Annotated[str | None, typer.Option("--person", help="Person surname.")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max results.")] = 20,
    live: Annotated[bool, typer.Option("--live", help="Use live HTTP.")] = False,
    full: Annotated[bool, typer.Option("--full", help="Fetch and parse each case card.")] = False,
) -> None:
    """Search for cases on sud_delo (diagnostic command)."""
    from court_monitor.sources.sudrf_case_search import SudrfCaseSearchAdapter  # noqa: PLC0415
    from court_monitor.sources.sudrf_dto import SudrfCaseSearchCriteria  # noqa: PLC0415

    src = get_source(court)
    if src is None:
        typer.echo(f"Источник «{court}» не найден.", err=True)
        raise typer.Exit(code=1)

    if live and src.backend != SourceBackend.http:
        from dataclasses import replace as dcreplace  # noqa: PLC0415

        src = dcreplace(src, backend=SourceBackend.http)

    result_date_value = _parse_cli_date(result_date or date)
    event_date_value = _parse_cli_date(event_date)

    criteria = SudrfCaseSearchCriteria(
        court=src.name,
        article=article,
        result_date=result_date_value,
        event_date=event_date_value,
        case_number=case_number,
        person_name=person,
        limit=limit,
    )
    adapter = SudrfCaseSearchAdapter(src)
    results = adapter.search(criteria)

    if not results:
        typer.echo("Результатов не найдено.")
        return

    typer.echo(f"Найдено результатов: {len(results)}\n")
    for i, r in enumerate(results):
        typer.echo(f"{i + 1:3}. [{r.case_number or '?'}] {r.url}")

    if full:
        from court_monitor.parsers.sud_delo import parse_case_card  # noqa: PLC0415

        for r in results:
            html = adapter.fetch_case_card_html(r)
            if not html:
                continue
            card = parse_case_card(html, case_uid=r.case_uid, court=src.court_name)
            typer.echo(f"\n--- {r.case_number} ---")
            typer.echo(f"  Суд: {card.court}")
            typer.echo(f"  Судья: {card.judge}")
            typer.echo(f"  Поступило: {card.received_at}")
            typer.echo(f"  Участники: {[p.name for p in card.persons]}")
            typer.echo(f"  События: {len(card.events)}")


def _parse_cli_date(raw: str | None):
    """Parse DD.MM.YYYY into :class:`datetime.date`, or exit with a clear error.

    Centralised so that invalid dates never produce raw Python tracebacks
    anywhere in the CLI — a wrong date is a user error, not a stack trace.
    """
    if not raw:
        return None
    from datetime import datetime as dt  # noqa: PLC0415

    try:
        return dt.strptime(raw, "%d.%m.%Y").date()
    except ValueError:
        typer.echo(
            f"Ошибка: дата «{raw}» должна быть в формате DD.MM.YYYY",
            err=True,
        )
        raise typer.Exit(code=2) from None


@app.command(name="process-court-cases")
def process_court_cases(
    court: Annotated[str, typer.Option("--court", help="Court identifier.")] = "2zovs",
    live: Annotated[
        bool,
        typer.Option("--live", help="Use live HTTP. Without --live, fixture backend only."),
    ] = False,
    reprocess: Annotated[
        bool,
        typer.Option(
            "--reprocess",
            help="Re-run documents already marked as processed_no_match (task §14).",
        ),
    ] = False,
) -> None:
    """Process pending court press releases through case matching pipeline.

    Without ``--live``: backend stays ``fixture`` (no network).
    With ``--live``: backend switches to ``http`` and real requests hit sudrf.
    Documents already marked ``processed_no_match`` are skipped unless
    ``--reprocess`` is given.
    """
    bootstrap_logging()
    require_current_schema()
    from court_monitor.services.court_orchestrator import (  # noqa: PLC0415
        process_all_pending_court_documents,
    )
    from court_monitor.storage.db import make_engine, session_scope  # noqa: PLC0415

    engine = make_engine()
    with session_scope(engine) as session:
        stats = process_all_pending_court_documents(
            session, court_name=court, force_live=live, reprocess=reprocess
        )
    typer.echo(f"Обработано документов: {stats.processed}")
    typer.echo(f"  review_created={stats.review_created}")
    typer.echo(f"  no_match={stats.no_match}")
    typer.echo(f"  temporary_failure={stats.temporary_failures}")
    typer.echo(f"  failed={stats.failed}")


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


# Command groups live in cli/commands/; each attaches its commands under their
# existing flat names so `court-monitor <command>` is unchanged.
_db_commands.register(app)
_documents_commands.register(app)
_records_commands.register(app)
_review_commands.register(app)

__all__ = ["app", "ImportPreview"]


if __name__ == "__main__":
    app()

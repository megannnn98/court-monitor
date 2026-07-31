"""Operator review from the terminal: match candidates, review items, audit."""

from __future__ import annotations

import getpass
import json
from typing import Annotated

import typer

from court_monitor.cli._shared import bootstrap_logging, require_current_schema
from court_monitor.matching.candidates import generate_matches
from court_monitor.observability import correlation_scope
from court_monitor.storage import repository as repo
from court_monitor.storage.db import make_engine, make_session_factory, session_scope


def generate_matches_cmd() -> None:
    """Generate match candidates between person facts and registry records."""
    bootstrap_logging()
    require_current_schema()
    engine = make_engine()
    with session_scope(engine) as session:
        stats = generate_matches(session)

    typer.echo(f"Документов обработано: {stats.get('documents_processed', '-')}")
    typer.echo(f"Фактов person: {stats['facts_person']}")
    typer.echo(f"Кандидатов создано: {stats['candidates_created']}")
    typer.echo(f"Уже существовало: {stats['already_existed']}")
    typer.echo(f"Без кандидатов: {stats['no_candidates']}")
    typer.echo(f"Ошибок: {stats['errors']}")


def list_matches_cmd(
    status: Annotated[
        str | None, typer.Option("--status", help="Filter by status (pending/confirmed/rejected).")
    ] = None,
    limit: Annotated[int, typer.Option(help="Max rows to print.")] = 50,
) -> None:
    """List match candidates."""
    bootstrap_logging()
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


def show_match_cmd(
    candidate_id: Annotated[int, typer.Argument(help="MatchCandidate.id")],
) -> None:
    """Show detailed match candidate information."""
    bootstrap_logging()
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


def _decide_match(candidate_id: int, *, status: str, comment: str, operator: str) -> None:
    """Record one operator decision. Confirm and reject differ only in ``status``."""
    bootstrap_logging()
    engine = make_engine()
    with session_scope(engine) as session, correlation_scope() as cid:
        c = repo.update_match_status(
            session,
            candidate_id,
            status,
            comment or None,
            actor=operator or _default_operator(),
            correlation_id=cid,
        )
        if c is None:
            typer.echo(f"Candidate {candidate_id} not found.", err=True)
            raise typer.Exit(code=1)
    typer.echo(f"Match {candidate_id} {status}.")


def confirm_match_cmd(
    candidate_id: Annotated[int, typer.Argument(help="MatchCandidate.id")],
    comment: Annotated[str, typer.Option("--comment", help="Review comment.")] = "",
    operator: Annotated[str, _OPERATOR_OPTION] = "",
) -> None:
    """Confirm a match candidate."""
    _decide_match(candidate_id, status="confirmed", comment=comment, operator=operator)


def reject_match_cmd(
    candidate_id: Annotated[int, typer.Argument(help="MatchCandidate.id")],
    comment: Annotated[str, typer.Option("--comment", help="Review comment.")] = "",
    operator: Annotated[str, _OPERATOR_OPTION] = "",
) -> None:
    """Reject a match candidate."""
    _decide_match(candidate_id, status="rejected", comment=comment, operator=operator)


def list_review_items_cmd(
    status: Annotated[
        str | None, typer.Option("--status", help="Filter by status (pending/resolved/dismissed).")
    ] = None,
    item_type: Annotated[str | None, typer.Option("--type", help="Filter by item_type.")] = None,
    limit: Annotated[int, typer.Option(help="Max rows to print.")] = 50,
) -> None:
    """List operator review items (parser failures, etc.)."""
    bootstrap_logging()
    engine = make_engine()
    factory = make_session_factory(engine)
    with factory() as session:
        items = repo.list_review_items(session, status=status, item_type=item_type, limit=limit)
        total = repo.count_review_items(session, status=status, item_type=item_type)

        label = "Всего review items"
        if status or item_type:
            label += " ("
            parts = []
            if status:
                parts.append(f"status={status}")
            if item_type:
                parts.append(f"type={item_type}")
            label += ", ".join(parts) + ")"
        typer.echo(f"{label}: {total}")
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


def resolve_review_item_cmd(
    item_id: Annotated[int, typer.Argument(help="ReviewItem.id")],
    comment: Annotated[str, typer.Option("--comment", help="Resolution comment.")] = "",
    dismiss: Annotated[
        bool, typer.Option("--dismiss", help="Mark as dismissed instead of resolved.")
    ] = False,
    operator: Annotated[str, _OPERATOR_OPTION] = "",
) -> None:
    """Mark a review item as resolved (or dismissed)."""
    bootstrap_logging()
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


def register(app: typer.Typer) -> None:
    app.command(name="generate-matches")(generate_matches_cmd)
    app.command(name="list-matches")(list_matches_cmd)
    app.command(name="show-match")(show_match_cmd)
    app.command(name="confirm-match")(confirm_match_cmd)
    app.command(name="reject-match")(reject_match_cmd)
    app.command(name="list-review-items")(list_review_items_cmd)
    app.command(name="resolve-review-item")(resolve_review_item_cmd)

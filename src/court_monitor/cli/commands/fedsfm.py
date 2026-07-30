"""Importing the Rosfinmonitoring list, from a file or from the live page."""

from __future__ import annotations

import hashlib
from pathlib import Path

import typer
from sqlalchemy import text

from court_monitor.services import import_rfm_records
from court_monitor.sources.fedsfm import load_fixture_rows, parse_file
from court_monitor.sources.fedsfm_live import (
    LIST_URL,
    FedsfmFetchError,
    fetch_live_html,
    parse_terrorists_html,
)
from court_monitor.storage.db import make_engine, make_session_factory, session_scope


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
        _import_fedsfm_live(dry_run=dry_run)
        return

    rows = load_fixture_rows()
    if not rows:
        typer.echo("No fixture data found.", err=True)
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo(f"fedsfm: total={len(rows)} imported=0 updated=0 duplicates=0")
        typer.echo("\n(режим --dry-run: данные не записаны)")
        return

    engine = make_engine()
    with session_scope(engine) as session:
        stats = import_rfm_records(session, rows, source_url=source_url)
    typer.echo(
        f"fedsfm: total={stats.total} imported={stats.imported} "
        f"updated={stats.updated} duplicates={stats.duplicates}"
    )


def _warn_on_dateless_rfm_records() -> None:
    """Warn that a prior dateless import will double up, not merge.

    Records are deduplicated by (source, normalized_name, birth_date). The CSV
    export carries no birth dates at all, while this page supplies one for
    every person — so the same human yields two different dedup keys and lands
    twice. Measured against a real CSV import: 21277 of 22156 names overlapped,
    and the live import reported duplicates=0. Silently doubling the registry
    would hand the operator two candidates per person to review, so say so
    before writing rather than after.
    """
    try:
        engine = make_engine()
        factory = make_session_factory(engine)
        with factory() as session:
            dateless = session.execute(
                text(
                    "SELECT COUNT(*) FROM person_records "
                    "WHERE source='rfm' AND (birth_date IS NULL OR birth_date='')"
                )
            ).scalar_one()
    except Exception:  # pragma: no cover - warning must never block the import
        return

    if not dateless:
        return

    typer.secho(
        f"\nВНИМАНИЕ: в базе уже есть {dateless} записей rfm без даты рождения "
        "(типично для импорта из CSV).\n"
        "Дедупликация идёт по (source, normalized_name, birth_date), поэтому эти записи "
        "НЕ будут объединены с загружаемыми — люди задвоятся, и оператор увидит по два "
        "кандидата на каждого.\n"
        "Живой перечень полнее (есть даты и места рождения), поэтому обычно старые записи "
        "стоит удалить перед импортом.",
        fg=typer.colors.YELLOW,
        bold=True,
        err=True,
    )


def _import_fedsfm_live(*, dry_run: bool) -> None:
    """Fetch and import the list straight from the published fedsfm.ru page."""
    try:
        html = fetch_live_html()
    except FedsfmFetchError as exc:
        typer.echo(f"Не удалось загрузить перечень: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    result = parse_terrorists_html(html)
    _print_fedsfm_preview(
        f"{LIST_URL} (live)",
        hashlib.sha256(html.encode("utf-8")).hexdigest(),
        len(html.encode("utf-8")),
        result,
    )

    if not result.rows:
        typer.echo(
            "Ни одной записи о физлице не распознано — вероятно, изменилась вёрстка страницы.",
            err=True,
        )
        raise typer.Exit(code=1)

    _warn_on_dateless_rfm_records()

    if dry_run:
        typer.echo("\n(режим --dry-run: данные не записаны)")
        return

    engine = make_engine()
    with session_scope(engine) as session:
        stats = import_rfm_records(session, result.rows, source="rfm", source_url=LIST_URL)
    typer.echo(
        f"\nfedsfm: total={stats.total} imported={stats.imported} "
        f"updated={stats.updated} duplicates={stats.duplicates}"
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
        f"\nfedsfm: total={stats.total} imported={stats.imported} "
        f"updated={stats.updated} duplicates={stats.duplicates}"
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

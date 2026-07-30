"""Reference data: registry sources and imported person records."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from selectolax.parser import HTMLParser

from court_monitor.cli._shared import REPO_ROOT, bootstrap_logging
from court_monitor.domain.models import FetchHealth
from court_monitor.observability import get_logger
from court_monitor.sources.http_client import HttpClient
from court_monitor.storage import repository as repo
from court_monitor.storage.db import make_engine, make_session_factory

# ---------------------------------------------------------------------------
# list-sources + fetch-demo-source
# ---------------------------------------------------------------------------


def list_sources() -> None:
    """List sources from the Airtable fixture (ID | Name | URL)."""
    fixture = REPO_ROOT / "tests" / "fixtures" / "airtable" / "source_registry.json"
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
    fixture = REPO_ROOT / "tests" / "fixtures" / "airtable" / "source_registry.json"
    if not fixture.exists():
        typer.echo(f"Fixture not found: {fixture}", err=True)
        raise typer.Exit(code=1)
    with fixture.open() as f:
        return json.load(f)


def _find_first_reachable(rows: list[dict]) -> tuple[str, str]:
    """Find the first reachable URL and return (url, html)."""
    log = get_logger("cli.fetch_demo")
    with HttpClient() as client:
        for row in rows:
            url = row.get("url", "")
            if not url:
                continue
            try:
                resp = client.get(url)
                if resp.health == FetchHealth.ok and resp.text:
                    return url, resp.text
                log.info("demo.source.unusable", url=url, health=str(resp.health))
            except Exception as exc:
                # Without this the operator just gets "no reachable source"
                # and no way to tell which one failed or why.
                log.warning("demo.source.failed", url=url, error=f"{type(exc).__name__}: {exc}")
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

    out_dir = REPO_ROOT / "tests" / "fixtures" / "demo"
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


def list_person_records_cmd(
    source: Annotated[str, typer.Option("--source", help="Filter by source (e.g. rfm).")] = "rfm",
    limit: Annotated[int, typer.Option(help="Max rows to print.")] = 50,
) -> None:
    """List person records from external registries."""
    bootstrap_logging()
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


def show_person_record_cmd(
    record_id: Annotated[int, typer.Argument(help="PersonRecord.id")],
) -> None:
    """Show a person record in detail."""
    bootstrap_logging()
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


def register(app: typer.Typer) -> None:
    app.command(name="list-sources")(list_sources)
    app.command(name="fetch-demo-source")(fetch_demo_source)
    app.command(name="list-person-records")(list_person_records_cmd)
    app.command(name="show-person-record")(show_person_record_cmd)

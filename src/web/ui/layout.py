"""The operator console's page frame and small HTML helpers."""

import hashlib
from html import escape
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.orm_models import (
    EntityGroupPoliticsRecord,
    MonitoringRunRecord,
    ParsedArticleRecord,
    PersonRecord,
    PersonResolutionDecisionRecord,
)

router = APIRouter()

# The stylesheet URL changes with its content: a browser never keeps a stale copy
# after a deployment and never re-downloads an unchanged one.
_CSS_VERSION = hashlib.sha256(
    (Path(__file__).resolve().parents[2] / "static" / "local-ui.css").read_bytes()
).hexdigest()[:12]


def _status_counts(db: Session) -> dict[str, object]:
    pending = db.scalar(
        select(func.count())
        .select_from(PersonResolutionDecisionRecord)
        .where(PersonResolutionDecisionRecord.status == "pending_review")
    )
    latest_run = db.scalars(
        select(MonitoringRunRecord).order_by(MonitoringRunRecord.started_at.desc()).limit(1)
    ).first()
    return {
        "articles": db.scalar(select(func.count()).select_from(ParsedArticleRecord)) or 0,
        "persons": db.scalar(select(func.count()).select_from(PersonRecord)) or 0,
        "pending_reviews": pending or 0,
        "latest_run": latest_run.status if latest_run is not None else "нет",
        "result": db.scalar(
            select(func.count())
            .select_from(EntityGroupPoliticsRecord)
            .where(EntityGroupPoliticsRecord.verdict == "political")
        )
        or 0,
    }


# The home page: the pipeline's steps, where every run starts.
HOME = "/ui/management"

# Menu icons: Lucide's (lucide.dev, ISC), inline so the page needs no file; they take the
# text's colour.
_ICONS = {
    "home": '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
    '<polyline points="9 22 9 12 15 12 15 22"/>',
    "entities": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
    '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "disputes": '<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/>'
    '<path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M7 21h10"/>'
    '<path d="M12 3v18"/><path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>',
    "political": '<line x1="8" x2="21" y1="6" y2="6"/><line x1="8" x2="21" y1="12" y2="12"/>'
    '<line x1="8" x2="21" y1="18" y2="18"/><line x1="3" x2="3.01" y1="6" y2="6"/>'
    '<line x1="3" x2="3.01" y1="12" y2="12"/><line x1="3" x2="3.01" y1="18" y2="18"/>',
    "officials": '<path d="m14 13-7.5 7.5c-.83.83-2.17.83-3 0a2.12 2.12 0 0 1 0-3L11 10"/>'
    '<path d="m16 16 6-6"/><path d="m8 8 6-6"/><path d="m9 7 8 8"/><path d="m21 11-8-8"/>',
    "logs": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
    '<path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/>'
    '<path d="M16 17H8"/>',
    "wiki": '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>'
    '<path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
}


def _icon(name: str) -> str:
    return (
        '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f"{_ICONS[name]}</svg>"
    )


def _page(
    title: str,
    body: str,
    *,
    active: str,
    instruction: str,
    next_action: str,
    db: Session,
    warning: str | None = None,
) -> HTMLResponse:
    counts = _status_counts(db)
    # «Управление» is the home page, apart above the sections; «Кандидаты» is hidden
    # for now (the page still answers by its link).
    # The tools that make and check «Результат», below it.
    tools = [
        ("entities", "Сущности", "/ui/entities"),
        ("disputes", "Спорные случаи", "/ui/disputes"),
    ]
    # Looked at now and then: at the bottom of the menu.
    reference = [
        ("officials", "Должностные лица", "/ui/officials"),
        ("logs", "Логи", "/ui/logs"),
        ("wiki", "Вики", "/ui/wiki"),
    ]
    home = (
        f'<a class="home{" active" if active == "management" else ""}" '
        f'href="{HOME}">{_icon("home")}<span>Главная: Управление</span></a>'
    )
    # What the pipeline is for: right under the home page, with its count.
    result = (
        f'<a class="result{" active" if active == "political" else ""}" href="/ui/political">'
        f"{_icon('political')}<span>Результат</span>"
        f'<span class="nav-count">{counts["result"]}</span></a>'
    )

    def plain(items: list[tuple[str, str, str]]) -> str:
        return "\n".join(
            f'<a class="{"active" if key == active else ""}" href="{href}">'
            f"{_icon(key)}<span>{label}</span></a>"
            for key, label, href in items
        )

    links = (
        home
        + result
        + '<div class="nav-group">Инструменты</div>'
        + plain(tools)
        + f'<div class="nav-bottom">{plain(reference)}</div>'
    )
    warning_html = f'<p class="warning">{escape(warning)}</p>' if warning else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <link rel="stylesheet" href="/static/local-ui.css?v={_CSS_VERSION}">
</head>
<body>
  <aside>
    <a class="brand" href="{HOME}">court-monitor</a>
    <nav>{links}</nav>
  </aside>
  <main>
    <section class="status-strip">
      <span>Статьи: <strong>{counts["articles"]}</strong></span>
      <span>Persons: <strong>{counts["persons"]}</strong></span>
      <span>ER pending: <strong>{counts["pending_reviews"]}</strong></span>
      <span>Последний run: <strong>{escape(str(counts["latest_run"]))}</strong></span>
    </section>
    <section class="instruction">
      <h1>{escape(title)}</h1>
      <p>{escape(instruction)}</p>
      <p><strong>Дальше:</strong> {escape(next_action)}</p>
      {warning_html}
    </section>
    {body}
  </main>
</body>
</html>"""
    )


def _fmt(value: object) -> str:
    return "—" if value is None or value == "" else escape(str(value))


def _button(action_url: str, label: str) -> str:
    return (
        f'<form method="post" action="{escape(action_url)}"><button>{escape(label)}</button></form>'
    )

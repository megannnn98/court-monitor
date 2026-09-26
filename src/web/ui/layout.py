"""The operator console's page frame and small HTML helpers."""

import hashlib
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.orm_models import (
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    MonitoringRunRecord,
    ParsedArticleRecord,
)
from web.ui.workload import workload

router = APIRouter()

# The stylesheet URL changes with its content: a browser never keeps a stale copy
# after a deployment and never re-downloads an unchanged one.
_CSS_VERSION = hashlib.sha256(
    (Path(__file__).resolve().parents[2] / "static" / "local-ui.css").read_bytes()
).hexdigest()[:12]


def _status_counts(db: Session) -> dict[str, object]:
    latest_run = db.scalars(
        select(MonitoringRunRecord).order_by(MonitoringRunRecord.started_at.desc()).limit(1)
    ).first()
    return {
        "articles": db.scalar(select(func.count()).select_from(ParsedArticleRecord)) or 0,
        "people": db.scalar(select(func.count()).select_from(EntityGroupRecord)) or 0,
        "queue": workload(db).total,
        "latest_run": _run_status_label(latest_run.status if latest_run is not None else None),
        "result": db.scalar(
            select(func.count())
            .select_from(EntityGroupPoliticsRecord)
            .where(EntityGroupPoliticsRecord.verdict == "political")
        )
        or 0,
    }


def _run_status_label(status: object | None) -> str:
    value = getattr(status, "value", status)
    return {
        "pending": "в очереди",
        "running": "выполняется",
        "completed": "завершён",
        "completed_with_errors": "завершён с ошибками",
        "failed": "ошибка",
        "aborted": "остановлен",
    }.get(str(value), "нет" if value is None else str(value))


# The home page: what is found and what waits for the operator.
HOME = "/ui/overview"

# Menu icons: Lucide's (lucide.dev, ISC), inline so the page needs no file; they take the
# text's colour.
_ICONS = {
    "overview": '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
    '<polyline points="9 22 9 12 15 12 15 22"/>',
    "investigations": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "entities": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
    '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "publications": '<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 '
    '1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8"/><path d="M15 18h-5"/>'
    '<path d="M10 6h8v4h-8V6Z"/>',
    "unnamed": '<circle cx="10" cy="7" r="4"/><path d="M10.3 15H7a4 4 0 0 0-4 4v2"/>'
    '<circle cx="17" cy="17" r="3"/><path d="m21 21-1.9-1.9"/>',
    "queue": '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 '
    '2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    "political": '<line x1="8" x2="21" y1="6" y2="6"/><line x1="8" x2="21" y1="12" y2="12"/>'
    '<line x1="8" x2="21" y1="18" y2="18"/><line x1="3" x2="3.01" y1="6" y2="6"/>'
    '<line x1="3" x2="3.01" y1="12" y2="12"/><line x1="3" x2="3.01" y1="18" y2="18"/>',
    "management": '<line x1="21" x2="14" y1="4" y2="4"/><line x1="10" x2="3" y1="4" y2="4"/>'
    '<line x1="21" x2="12" y1="12" y2="12"/><line x1="8" x2="3" y1="12" y2="12"/>'
    '<line x1="21" x2="16" y1="20" y2="20"/><line x1="12" x2="3" y1="20" y2="20"/>'
    '<line x1="14" x2="14" y1="2" y2="6"/><line x1="8" x2="8" y1="10" y2="14"/>'
    '<line x1="16" x2="16" y1="18" y2="22"/>',
    "officials": '<path d="m14 13-7.5 7.5c-.83.83-2.17.83-3 0a2.12 2.12 0 0 1 0-3L11 10"/>'
    '<path d="m16 16 6-6"/><path d="m8 8 6-6"/><path d="m9 7 8 8"/><path d="m21 11-8-8"/>',
    "logs": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
    '<path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/>'
    '<path d="M16 17H8"/>',
    "wiki": '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>'
    '<path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
    "menu": '<line x1="4" x2="20" y1="6" y2="6"/><line x1="4" x2="20" y1="12" y2="12"/>'
    '<line x1="4" x2="20" y1="18" y2="18"/>',
}


def _icon(name: str) -> str:
    return (
        '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f"{_ICONS[name]}</svg>"
    )


# The menu: the person and the evidence first; the pipeline under «Система».
_SECTIONS = (
    ("overview", "Обзор", HOME),
    ("investigations", "Расследование", "/ui/investigations"),
    ("entities", "Люди", "/ui/entities"),
    ("unnamed", "Безымянные", "/ui/unnamed"),
    ("publications", "Публикации", "/ui/publications"),
    ("queue", "Очередь", "/ui/queue"),
)
_SYSTEM = (
    ("management", "Управление", "/ui/management"),
    ("officials", "Должностные лица", "/ui/officials"),
    ("logs", "Логи", "/ui/logs"),
    ("wiki", "Вики", "/ui/wiki"),
)
# Pages that belong to a section without an item of their own.
_ACTIVE_ALIASES = {"disputes": "queue"}


def _nav(active: str, counts: dict[str, object]) -> str:
    active = _ACTIVE_ALIASES.get(active, active)

    def link(key: str, label: str, href: str, count: object = None, css: str = "") -> str:
        classes = " ".join(part for part in (css, "active" if key == active else "") if part)
        current = ' aria-current="page"' if key == active else ""
        badge = f'<span class="nav-count">{count}</span>' if count is not None else ""
        return (
            f'<a class="{classes}" href="{href}"{current}>{_icon(key)}'
            f"<span>{escape(label)}</span>{badge}</a>"
        )

    main = [link(*_SECTIONS[0])]
    # What the whole pipeline is for: apart, right under the overview, with its count.
    main.append(link("political", "Результат", "/ui/political", counts["result"], "result"))
    for key, label, href in _SECTIONS[1:]:
        main.append(link(key, label, href, counts["queue"] if key == "queue" else None))
    system = "".join(link(*item) for item in _SYSTEM)
    return (
        "".join(main)
        + f'<div class="nav-bottom"><div class="nav-group">Система</div>{system}</div>'
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
    warning_html = f'<p class="warning">{escape(warning)}</p>' if warning else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{escape(title)}</title>
  <link rel="stylesheet" href="/static/local-ui.css?v={_CSS_VERSION}">
  <script>document.documentElement.classList.add("js")</script>
</head>
<body>
  <a class="skip-link" href="#content">К содержанию</a>
  <aside>
    <div class="aside-head">
      <a class="brand" href="{HOME}">court-monitor</a>
      <span class="brand-caption">система расследований</span>
      <button type="button" class="nav-toggle" aria-label="Меню" aria-controls="main-nav"
        aria-expanded="false"
        onclick="const open = document.body.classList.toggle('nav-open');
          this.setAttribute('aria-expanded', open)">{_icon("menu")}</button>
    </div>
    <nav id="main-nav" aria-label="Разделы">{_nav(active, counts)}</nav>
  </aside>
  <main id="content" tabindex="-1">
    <section class="status-strip" aria-label="Показатели">
      <span><small>Публикации</small><strong>{counts["articles"]}</strong></span>
      <span><small>Люди</small><strong>{counts["people"]}</strong></span>
      <span><small>Результат</small><strong>{counts["result"]}</strong></span>
      <span><small>Очередь</small><strong>{counts["queue"]}</strong></span>
      <span><small>Последний запуск</small><strong>{escape(str(counts["latest_run"]))}</strong></span>
    </section>
    <header class="page-head">
      <h1>{escape(title)}</h1>
      <!-- What the page is and what to do next: at hand, not in the way. -->
      <details class="hint">
        <summary title="Как это работает"><span class="hint-mark" aria-hidden="true">?</span>
          Как это работает</summary>
        <div class="hint-body">
          <p>{escape(instruction)}</p>
          <p><strong>Дальше:</strong> {escape(next_action)}</p>
        </div>
      </details>
    </header>
    {warning_html}
    {body}
  </main>
</body>
</html>"""
    )


def pager(path: str, params: dict[str, str], page: int, pages: int) -> str:
    """«Назад · 1 · … · 12 13 14 · … · 248 · Вперёд»: the first, the last and the pages
    around the current one; every link keeps the list's filters."""
    if pages <= 1:
        return ""

    def link(number: int, label: str, rel: str = "") -> str:
        query = urlencode({**params, "page": number})
        return f'<a href="{path}?{escape(query, quote=True)}"{rel}>{label}</a>'

    shown = sorted({1, pages, *range(max(1, page - 2), min(pages, page + 2) + 1)})
    parts: list[str] = []
    if page > 1:
        parts.append(link(page - 1, "Назад", ' rel="prev"'))
    previous = 0
    for number in shown:
        if number - previous > 1:
            parts.append('<span class="gap">…</span>')
        parts.append(
            f'<b aria-current="page">{number}</b>' if number == page else link(number, str(number))
        )
        previous = number
    if page < pages:
        parts.append(link(page + 1, "Вперёд", ' rel="next"'))
    return f'<div class="pager" role="navigation" aria-label="Страницы">{" ".join(parts)}</div>'


def external_url(url: str | None) -> str | None:
    """A scraped address fit for a link: http(s) only, never `javascript:` or the like."""
    if url and url.strip().lower().startswith(("http://", "https://")):
        return url.strip()
    return None


def _fmt(value: object) -> str:
    return "—" if value is None or value == "" else escape(str(value))


def _button(action_url: str, label: str) -> str:
    return (
        f'<form method="post" action="{escape(action_url)}"><button>{escape(label)}</button></form>'
    )

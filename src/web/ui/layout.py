"""The operator console's page frame and small HTML helpers."""

from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.orm_models import (
    MonitoringRunRecord,
    ParsedArticleRecord,
    PersonRecord,
    PersonResolutionDecisionRecord,
)

router = APIRouter()


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
    }


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
    nav = [
        ("review", "ER-ревью", "/ui/person-resolution/reviews"),
        ("candidates", "Кандидаты", "/ui/candidates"),
        ("channel", "Для канала", "/ui/channel"),
        ("search", "Поиск", "/ui/search"),
        ("operations", "Операции", "/ui/operations"),
        ("monitoring", "Monitoring", "/ui/monitoring"),
        ("wiki", "Wiki", "/ui/wiki"),
    ]
    links = "\n".join(
        f'<a class="{"active" if key == active else ""}" href="{href}">{label}</a>'
        for key, label, href in nav
    )
    warning_html = f'<p class="warning">{escape(warning)}</p>' if warning else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <link rel="stylesheet" href="/static/local-ui.css">
</head>
<body>
  <aside>
    <div class="brand">court-monitor</div>
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

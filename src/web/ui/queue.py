"""Focused review pages for unclear roles and political classifications."""

from __future__ import annotations

import logging
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import EntityGroupPoliticsRecord, EntityGroupRecord, EntityGroupRoleRecord
from entities.disputes import reset_decisions
from entities.politics import UNCLEAR as UNCLEAR_VERDICT
from entities.roles import UNCLEAR as UNCLEAR_ROLE
from web.dependencies import get_db
from web.ui.entities import display_name
from web.ui.layout import _page

router = APIRouter()
logger = logging.getLogger("entities")

LIST_LIMIT = 100


def _entity_rows(rows: list[tuple[str, str, str]], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{empty}</p>'
    body = "".join(
        f'<tr><td><a href="/ui/investigations/{quote(key)}">{escape(display_name(name))}</a></td>'
        f"<td>{escape(reason)}</td></tr>"
        for key, name, reason in rows
    )
    return (
        '<table><thead><tr><th scope="col">Человек</th><th scope="col">Почему не решено</th>'
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def _review_page(
    title: str,
    explanation: str,
    rows: list[tuple[str, str, str]],
    db: Session,
) -> HTMLResponse:
    body = f"""<p><a href="/ui/cycle">Назад к циклу</a></p>
<section class="band">
  <h2>{escape(title)}</h2>
  <p class="muted">{escape(explanation)}</p>
  {_entity_rows(rows, f"{title}: открытых случаев нет.")}
</section>"""
    return _page(
        title,
        body,
        active="queue",
        instruction="Проверка результата модели по досье и исходным цитатам.",
        db=db,
    )


@router.get("/ui/roles", response_class=HTMLResponse)
def ui_roles(db: Session = Depends(get_db)) -> HTMLResponse:  # noqa: B008
    rows = [
        (key, name, reason)
        for key, name, reason in db.execute(
            select(EntityGroupRecord.key, EntityGroupRecord.name, EntityGroupRoleRecord.reason)
            .join(EntityGroupRoleRecord, EntityGroupRoleRecord.group_id == EntityGroupRecord.id)
            .where(EntityGroupRoleRecord.role == UNCLEAR_ROLE)
            .order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key)
            .limit(LIST_LIMIT)
        ).all()
    ]
    return _review_page(
        "Неясная роль в деле",
        "Шаг 4 не понял, заведено ли на человека дело. Ручного решения пока нет: откройте досье и проверьте цитаты.",
        rows,
        db,
    )


@router.get("/ui/politics-review", response_class=HTMLResponse)
def ui_politics_review(db: Session = Depends(get_db)) -> HTMLResponse:  # noqa: B008
    rows = [
        (key, name, reason)
        for key, name, reason in db.execute(
            select(
                EntityGroupRecord.key,
                EntityGroupRecord.name,
                EntityGroupPoliticsRecord.reason,
            )
            .join(
                EntityGroupPoliticsRecord,
                EntityGroupPoliticsRecord.group_id == EntityGroupRecord.id,
            )
            .where(EntityGroupPoliticsRecord.verdict == UNCLEAR_VERDICT)
            .order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key)
            .limit(LIST_LIMIT)
        ).all()
    ]
    return _review_page(
        "Неясная политичность",
        "Шаг 5 не смог отнести дело ни к политическим, ни к обычным уголовным. Ручного решения пока нет.",
        rows,
        db,
    )


@router.get("/ui/queue", response_class=RedirectResponse)
def legacy_queue(request: Request) -> RedirectResponse:
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/ui/pairs{query}", status_code=303)


@router.post("/ui/queue/reset-decisions", response_model=None)
@router.post("/ui/pairs/reset-decisions", response_model=None)
def reset_pair_decisions(db: Session = Depends(get_db)) -> RedirectResponse:  # noqa: B008
    """Forget every decision on a pair: the operator starts the pairs over."""
    deleted = reset_decisions(db)
    db.commit()
    logger.info("event=pair_decisions_reset deleted=%d", deleted)
    return RedirectResponse(f"/ui/pairs?reset={deleted}", status_code=303)

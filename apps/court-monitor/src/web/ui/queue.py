"""Focused review pages for unclear roles and political classifications.

Both read what steps 4 and 5 were unsure about and let the operator settle it: the
decision is applied at once and kept by the entity key, so the next rebuild of step 4 or
step 5 writes it again (`processing.roles.roles`, `analysis.politics.classification`).
"""

from __future__ import annotations

from html import escape
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from application import build_identity_commands
from web.dependencies import get_db
from web.ui.layout import _page
from web.views.entities import display_name
from web.views.queue import (
    _SECONDARY,
    POLITICS_EXPLANATION,
    POLITICS_TITLE,
    ROLE_CHOICES,
    ROLES_EXPLANATION,
    ROLES_TITLE,
    VERDICT_CHOICES,
    logger,
    settle_politics,
    settle_role,
    unclear_roles,
    unclear_verdicts,
)

router = APIRouter()


def _decide_form(action: str, key: str, field: str, choices: tuple[tuple[str, str], ...]) -> str:
    """One row's decision: a button per choice, the first one primary."""
    buttons = "".join(
        f'<button name="{field}" value="{escape(value, quote=True)}" type="submit"'
        f"{_SECONDARY if index else ''}>{escape(label)}</button>"
        for index, (value, label) in enumerate(choices)
    )
    return (
        f'<form method="post" action="{action}" class="decide-bar">'
        f'<input type="hidden" name="key" value="{escape(key, quote=True)}">'
        f"{buttons}</form>"
    )


def _entity_rows(
    rows: list[tuple[str, str, str]],
    empty: str,
    action: str,
    field: str,
    choices: tuple[tuple[str, str], ...],
) -> str:
    if not rows:
        return f'<p class="empty">{empty}</p>'
    body = "".join(
        f'<tr><td><a href="/ui/investigations/{quote(key)}">{escape(display_name(name))}</a></td>'
        f"<td>{escape(reason)}</td>"
        f"<td>{_decide_form(action, key, field, choices)}</td></tr>"
        for key, name, reason in rows
    )
    return (
        '<table><thead><tr><th scope="col">Человек</th><th scope="col">Почему не решено</th>'
        f'<th scope="col">Решение</th></tr></thead><tbody>{body}</tbody></table>'
    )


def _review_page(
    title: str,
    explanation: str,
    rows: list[tuple[str, str, str]],
    db: Session,
    action: str,
    field: str,
    choices: tuple[tuple[str, str], ...],
) -> HTMLResponse:
    body = f"""<p><a href="/ui/cycle">Назад к циклу</a></p>
<section class="band">
  <h2>{escape(title)}</h2>
  <p class="muted">{escape(explanation)}</p>
  {_entity_rows(rows, f"{title}: открытых случаев нет.", action, field, choices)}
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
    return _review_page(
        ROLES_TITLE,
        ROLES_EXPLANATION,
        unclear_roles(db),
        db,
        "/ui/roles/decide",
        "role",
        ROLE_CHOICES,
    )


@router.get("/ui/politics-review", response_class=HTMLResponse)
def ui_politics_review(db: Session = Depends(get_db)) -> HTMLResponse:  # noqa: B008
    return _review_page(
        POLITICS_TITLE,
        POLITICS_EXPLANATION,
        unclear_verdicts(db),
        db,
        "/ui/politics-review/decide",
        "verdict",
        VERDICT_CHOICES,
    )


@router.post("/ui/roles/decide", response_model=None)
async def decide_entity_role(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """A person's word on an entity's role: applied at once and kept for every rebuild."""
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    settle_role(db, (form.get("key") or [""])[0], (form.get("role") or [""])[0])
    return RedirectResponse("/ui/roles", status_code=303)


@router.post("/ui/politics-review/decide", response_model=None)
async def decide_entity_politics(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """A person's word on a case's politics: applied at once and kept for every rebuild."""
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    settle_politics(db, (form.get("key") or [""])[0], (form.get("verdict") or [""])[0])
    return RedirectResponse("/ui/politics-review", status_code=303)


@router.get("/ui/queue", response_class=RedirectResponse)
def legacy_queue(request: Request) -> RedirectResponse:
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/ui/pairs{query}", status_code=303)


@router.post("/ui/queue/reset-decisions", response_model=None)
@router.post("/ui/pairs/reset-decisions", response_model=None)
def reset_pair_decisions(db: Session = Depends(get_db)) -> RedirectResponse:  # noqa: B008
    """Forget every decision on a pair: the operator starts the pairs over."""
    deleted = build_identity_commands().reset_decisions(db)
    db.commit()
    logger.info("event=pair_decisions_reset deleted=%d", deleted)
    return RedirectResponse(f"/ui/pairs?reset={deleted}", status_code=303)

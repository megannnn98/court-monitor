"""Operator web UI — server-rendered, no build step.

Kept separate from :mod:`court_monitor.api`, which is a read-only JSON surface.
This one mutates: it is where an operator confirms or rejects a match between a
person named in an article and a record in the Rosfinmonitoring registry.

Because those decisions are consequential and there is no authentication yet
(D-007), the server binds to loopback by default and every mutating route goes
through ``require_operator`` + ``verify_csrf``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from court_monitor import __version__
from court_monitor.config.settings import settings
from court_monitor.observability import configure_logging, correlation_scope, get_logger
from court_monitor.storage import repository as repo
from court_monitor.web.deps import (
    CSRF_FIELD,
    csrf_token,
    get_session,
    require_operator,
    verify_csrf,
)

_HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = _HERE / "templates"
STATIC_DIR = _HERE / "static"

configure_logging(settings.log_level)
_log = get_logger("web")

app = FastAPI(title="court-monitor UI", version=__version__, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

PAGE_SIZE = 50

SessionDep = Annotated[Session, Depends(get_session)]
CsrfDep = Annotated[None, Depends(verify_csrf)]


def _render(request: Request, template: str, session: Session, **ctx: Any) -> HTMLResponse:
    """Render with the chrome every page needs (operator, queue badges)."""
    base = {
        "operator": require_operator(),
        "csrf_token": csrf_token(),
        "csrf_field": CSRF_FIELD,
        "pending_matches": repo.count_match_candidates(session, status="pending"),
        "pending_reviews": repo.count_review_items(session, status="pending"),
    }
    return templates.TemplateResponse(request, template, {**base, **ctx})


@app.get("/", response_class=HTMLResponse, name="dashboard")
def dashboard(request: Request, session: SessionDep) -> HTMLResponse:
    return _render(
        request,
        "dashboard.html",
        session,
        nav="dashboard",
        documents=repo.count_documents(session),
        relevant=repo.count_relevant_documents(session),
        facts=repo.count_facts(session),
        person_records=repo.count_person_records(session),
        matches_pending=repo.count_match_candidates(session, status="pending"),
        matches_confirmed=repo.count_match_candidates(session, status="confirmed"),
        matches_rejected=repo.count_match_candidates(session, status="rejected"),
        reviews_pending=repo.count_review_items(session, status="pending"),
        recent=repo.list_documents(session, limit=10),
    )


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@app.get("/documents", response_class=HTMLResponse, name="documents")
def documents(request: Request, session: SessionDep, page: int = 1) -> HTMLResponse:
    page = max(1, page)
    offset = (page - 1) * PAGE_SIZE
    rows = repo.list_documents(session, limit=PAGE_SIZE, offset=offset)
    return _render(
        request,
        "documents.html",
        session,
        nav="documents",
        rows=rows,
        page=page,
        total=repo.count_documents(session),
        page_size=PAGE_SIZE,
        has_next=len(rows) == PAGE_SIZE,
    )


@app.get("/documents/{document_id}", response_class=HTMLResponse, name="document_detail")
def document_detail(request: Request, document_id: int, session: SessionDep) -> HTMLResponse:
    doc = repo.get_document(session, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    grouped: dict[str, list[Any]] = {}
    for fact in doc.facts:
        grouped.setdefault(fact.field, []).append(fact)
    return _render(
        request,
        "document_detail.html",
        session,
        nav="documents",
        doc=doc,
        grouped=grouped,
    )


# ---------------------------------------------------------------------------
# Match review
# ---------------------------------------------------------------------------


@app.get("/matches", response_class=HTMLResponse, name="matches")
def matches(request: Request, session: SessionDep, status: str = "pending") -> HTMLResponse:
    selected = status if status in {"pending", "confirmed", "rejected"} else None
    return _render(
        request,
        "matches.html",
        session,
        nav="matches",
        rows=repo.list_match_candidates(session, status=selected, limit=200),
        status=selected,
        counts={
            "pending": repo.count_match_candidates(session, status="pending"),
            "confirmed": repo.count_match_candidates(session, status="confirmed"),
            "rejected": repo.count_match_candidates(session, status="rejected"),
        },
    )


@app.get("/matches/{candidate_id}", response_class=HTMLResponse, name="match_detail")
def match_detail(request: Request, candidate_id: int, session: SessionDep) -> HTMLResponse:
    candidate = repo.get_match_candidate(session, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Кандидат не найден")
    return _render(
        request,
        "match_detail.html",
        session,
        nav="matches",
        c=candidate,
        fact=candidate.extracted_fact,
        record=candidate.person_record,
        reasons=_load_json(candidate.reasons_json),
        conflicts=_load_json(candidate.conflicts_json),
    )


@app.post("/matches/{candidate_id}/decide", name="match_decide")
def match_decide(
    candidate_id: int,
    decision: Annotated[str, Form()],
    _csrf: CsrfDep,
    session: SessionDep,
    comment: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Record the operator's decision. Never called by the pipeline itself."""
    if decision not in {"confirmed", "rejected"}:
        raise HTTPException(status_code=400, detail="Недопустимое решение")

    operator = require_operator()
    with correlation_scope() as cid:
        candidate = repo.update_match_status(
            session,
            candidate_id,
            decision,
            comment.strip() or None,
            actor=operator,
            correlation_id=cid,
        )
        if candidate is None:
            raise HTTPException(status_code=404, detail="Кандидат не найден")
        session.commit()
    _log.info("web.match.decided", candidate_id=candidate_id, decision=decision, actor=operator)
    return RedirectResponse(url="/matches?status=pending", status_code=303)


# ---------------------------------------------------------------------------
# Review items
# ---------------------------------------------------------------------------


@app.get("/review", response_class=HTMLResponse, name="review_items")
def review_items(request: Request, session: SessionDep, status: str = "pending") -> HTMLResponse:
    selected = status if status in {"pending", "resolved", "dismissed"} else None
    return _render(
        request,
        "review_items.html",
        session,
        nav="review",
        rows=repo.list_review_items(session, status=selected, limit=200),
        status=selected,
        counts={
            "pending": repo.count_review_items(session, status="pending"),
            "resolved": repo.count_review_items(session, status="resolved"),
            "dismissed": repo.count_review_items(session, status="dismissed"),
        },
    )


@app.post("/review/{item_id}/resolve", name="review_resolve")
def review_resolve(
    item_id: int,
    _csrf: CsrfDep,
    session: SessionDep,
    decision: Annotated[str, Form()] = "resolved",
    comment: Annotated[str, Form()] = "",
) -> RedirectResponse:
    if decision not in {"resolved", "dismissed"}:
        raise HTTPException(status_code=400, detail="Недопустимое решение")

    operator = require_operator()
    with correlation_scope() as cid:
        item = repo.resolve_review_item(
            session,
            item_id,
            status=decision,
            resolved_by=operator,
            comment=comment.strip() or None,
            correlation_id=cid,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Запись не найдена")
        session.commit()
    return RedirectResponse(url="/review?status=pending", status_code=303)


def _load_json(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:  # pragma: no cover - defensive
        return []
    return loaded if isinstance(loaded, list) else []

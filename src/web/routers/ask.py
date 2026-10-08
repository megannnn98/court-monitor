"""«Спросить» for the React console: the question, its answer and the counts it stands on,
and the questions asked before, as the legacy page shows them (`web.ui.ask`). Asking is an
action — it spends the day's budget — refused from other origins (`web.csrf`)."""

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from db.orm_models import ChatQuestionRecord
from entities.ask import MARK_CLOSE, MARK_OPEN, MAX_QUESTION, ask, daily_budget, spent_today
from web.dependencies import get_db, session_factory_for
from web.response_models import (
    AskAnswerResponse,
    AskedQuestionResponse,
    AskPageResponse,
    AskRequest,
    AskResponse,
)
from web.ui import ask as ask_page

router = APIRouter()


@router.get("/ask", response_model=AskPageResponse)
def get_ask(
    q: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> AskPageResponse:
    """The question `q`, or the latest one."""
    history = ask_page.asked_before(db)
    record = db.get(ChatQuestionRecord, q) if q else (history[0] if history else None)
    return AskPageResponse(
        spent_today=spent_today(db),
        budget=daily_budget(),
        max_question=MAX_QUESTION,
        examples=list(ask_page.EXAMPLES),
        mark_open=MARK_OPEN,
        mark_close=MARK_CLOSE,
        answer=AskAnswerResponse(
            id=record.id,
            question=record.question,
            answer=record.answer,
            outcome=record.outcome,
            asked_at=record.asked_at,
            model=record.model,
            cost_usd=record.cost_usd,
            unverified=ask_page.unverified_of(record),
            results=ask_page.results_of(record),
        )
        if record
        else None,
        history=[
            AskedQuestionResponse(id=one.id, question=one.question, asked_at=one.asked_at)
            for one in history
        ],
    )


@router.post("/ask", response_model=AskResponse)
async def post_ask(
    body: AskRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> AskResponse:
    """Ask a model; up to twenty seconds, in a thread so the other pages do not wait."""
    asked = await run_in_threadpool(
        ask,
        session_factory_for(db),
        ask_page.current_asker(),
        body.question,
        budget_usd=daily_budget(),
    )
    # Nothing was asked: the reason is all there is to show.
    return AskResponse(id=asked.id, note=None if asked.id is not None else asked.answer[:300])

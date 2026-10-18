"""The numbers every legacy page shows in its status strip, menu and live strip, read
through the same functions, so the React console says the same. Read-only."""

from __future__ import annotations

from sqlalchemy.orm import Session

from orchestrator.read import live_operation
from orchestrator.source_health import layout_suspects
from web.response_models import (
    BalanceResponse,
    LayoutSuspectResponse,
    LiveOperationResponse,
    QueueResponse,
    StatusResponse,
)
from web.ui import spend
from web.ui.layout import strip_numbers
from web.ui.run_cards import MODE_TITLES
from web.ui.work_cycle import live_next_action
from web.ui.workload import workload

__all__ = ["read_status"]


def read_status(db: Session) -> StatusResponse:
    work = workload(db)
    numbers = strip_numbers(db)
    operation = live_operation(db)
    live = (
        None
        if operation is None
        else LiveOperationResponse(
            mode=operation.mode, title=MODE_TITLES.get(operation.mode, MODE_TITLES[None])
        )
    )
    return StatusResponse(
        articles=numbers.articles,
        people=numbers.people,
        result=numbers.result,
        queue=QueueResponse(
            total=work.total,
            pairs=work.pairs,
            unclear_roles=work.unclear_roles,
            unclear_verdicts=work.unclear_verdicts,
            unnamed=work.unnamed,
            junk_holds=work.junk_holds,
        ),
        latest_monitoring_status=numbers.latest_monitoring_status,
        live_operation=live,
        next_action=live_next_action(db, work),
        balance=_balance(),
        layout_suspects=[
            LayoutSuspectResponse(
                source=suspect.source,
                signal=suspect.signal,
                run_id=suspect.run_id,
                since=suspect.since,
                count=suspect.count,
                total=suspect.total,
                text=suspect.text,
            )
            for suspect in layout_suspects(db)
        ],
    )


def _balance() -> BalanceResponse | None:
    found = spend.strip_balance()
    if found is None:
        return None
    return BalanceResponse(figure=found.figure, hint=found.hint, low=found.low, known=found.known)

"""The numbers every legacy page shows in its status strip, menu and live strip, read
through the same functions, so the React console says the same. Read-only."""

from __future__ import annotations

from sqlalchemy.orm import Session

from web.response_models import (
    BalanceResponse,
    LiveOperationResponse,
    QueueResponse,
    StatusResponse,
)
from web.ui import spend
from web.ui.layout import LIVE_RUN, strip_numbers
from web.ui.run_cards import MODE_TITLES
from web.ui.work_cycle import live_next_action
from web.ui.workload import workload

__all__ = ["read_status"]


def read_status(db: Session) -> StatusResponse:
    work = workload(db)
    numbers = strip_numbers(db)
    row = db.execute(LIVE_RUN).first()
    live = (
        None
        if row is None
        else LiveOperationResponse(mode=row[0], title=MODE_TITLES.get(row[0], MODE_TITLES[None]))
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
    )


def _balance() -> BalanceResponse | None:
    found = spend.strip_balance()
    if found is None:
        return None
    return BalanceResponse(figure=found.figure, hint=found.hint, low=found.low, known=found.known)

"""«Начать пары заново»: the React pairs page sends this legacy form's address."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from application import build_identity_commands
from web.dependencies import get_db

# The same logger the pair decisions write to (`web.views.queue`).
logger = logging.getLogger("entities")

router = APIRouter()


@router.post("/ui/queue/reset-decisions", response_model=None)
@router.post("/ui/pairs/reset-decisions", response_model=None)
def reset_pair_decisions(db: Session = Depends(get_db)) -> RedirectResponse:  # noqa: B008
    """Forget every decision on a pair: the operator starts the pairs over."""
    deleted = build_identity_commands().reset_decisions(db)
    db.commit()
    logger.info("event=pair_decisions_reset deleted=%d", deleted)
    return RedirectResponse(f"/ui/pairs?reset={deleted}", status_code=303)

"""The graph of an investigation, for the dossier's interactive view.

`GET /api/investigations/{key}/graph` is the person and their latest events;
`GET /api/investigations/{key}/graph/expand?node=event:781` is what one event or one
person adds. The handlers only translate: the graph is `web.investigation_graph`'s.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from web.dependencies import get_db
from web.investigation_graph import (
    NotExpandable,
    UnknownNode,
    expand,
    initial_graph,
    known_person,
)

router = APIRouter()


@router.get("/api/investigations/{key}/graph")
def investigation_graph(key: str, db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: B008
    try:
        return initial_graph(db, key)
    except UnknownNode as error:
        raise HTTPException(status_code=404, detail="Человек не найден") from error


@router.get("/api/investigations/{key}/graph/expand")
def investigation_graph_expand(
    key: str,
    node: str = Query(max_length=300),
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """`key` is the dossier the graph is open in: the answer does not depend on it, but an
    address under a person that does not exist is not served."""
    try:
        known_person(db, key)
        return expand(db, node)
    except UnknownNode as error:
        raise HTTPException(status_code=404, detail="Узел не найден") from error
    except NotExpandable as error:
        raise HTTPException(status_code=400, detail="Этот узел не раскрывается") from error

"""A person's card, written to by the React dossier at these legacy addresses: the name
corrected by hand and the official mark."""

from __future__ import annotations

from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from application import build_identity_commands, group_followers
from processing.roles.commands import mark_official_of
from web.dependencies import get_db

router = APIRouter()


@router.post("/ui/entities/{key}/name", response_model=None)
async def correct_entity_name(
    key: str,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """A person's correction of the name, applied at once and kept for every rebuild."""
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    try:
        corrected = build_identity_commands().correct_name(db, key, (form.get("name") or [""])[0])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Пустое имя") from exc
    if corrected is None:
        raise HTTPException(status_code=404, detail="Сущность не найдена")
    db.commit()
    return RedirectResponse(f"/ui/investigations/{quote(key)}", status_code=303)


@router.post("/ui/entities/{key}/official", response_model=None)
async def mark_entity_official(
    key: str,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """A person's word: this entity is (or is not) an official; applied at once."""
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    if not mark_official_of(
        db, key, (form.get("official") or [""])[0] == "yes", downstream=group_followers()
    ):
        raise HTTPException(status_code=404, detail="Сущность не найдена")
    db.commit()
    back = (form.get("back") or [""])[0]
    return RedirectResponse(
        "/ui/officials" if back == "officials" else f"/ui/investigations/{quote(key)}",
        status_code=303,
    )

"""«Без имени в базе» for the React console: the nameless records of the operator's base
and who on the list they may be, read and decided as the legacy page does
(`web.ui.base_unnamed`). A decision is an action, refused from other origins."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from entities.base_unnamed import NamelessCase
from entities.jurisdiction import Jurisdiction
from web.dependencies import get_db
from web.response_models import (
    BaseUnnamedCandidateResponse,
    BaseUnnamedCardResponse,
    BaseUnnamedDecisionRequest,
    BaseUnnamedDecisionResponse,
    BaseUnnamedListResponse,
    CourtHintResponse,
    OptionResponse,
)
from web.ui.base_unnamed import (
    _STATUSES,
    PAGE_SIZE,
    apply_decide,
    base_listing,
    case_courts,
    facts_text,
)
from web.ui.layout import external_url

router = APIRouter()


def _state(case: NamelessCase) -> tuple[str, str | None]:
    if case.identified is not None:
        return "identified", case.identified.entry.full_name
    if case.confirmed:
        return "confirmed", case.confirmed.split("|")[0]
    return ("open" if case.open else "rejected"), None


@router.get("/base-unnamed", response_model=BaseUnnamedListResponse)
def list_base_unnamed(
    status: str = Query(default="open", pattern="^(open|found|all)$"),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> BaseUnnamedListResponse:
    """One tab of «Без имени в базе», as the legacy page shows it."""
    cases, totals, chosen = base_listing(db, status)
    courts = Jurisdiction.from_session(db)
    items = []
    for case in chosen[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]:
        state, name = _state(case)
        hints = case_courts(courts, case)
        items.append(
            BaseUnnamedCardResponse(
                record=case.record.external_id,
                full_name=case.record.full_name,
                facts=facts_text(case),
                state=state,
                identified_as=name,
                confirmed=case.confirmed,
                candidates=[
                    BaseUnnamedCandidateResponse(
                        key=item.entry.key,
                        full_name=item.entry.full_name,
                        birth_date=item.entry.birth_date,
                        birth_place=item.entry.birth_place,
                        reasons=item.reasons,
                        decision=item.decision,
                    )
                    for item in case.candidates
                ],
                total=case.total,
                courts=[
                    CourtHintResponse(
                        court=hint.court, cases=hint.cases, site=external_url(hint.site)
                    )
                    for hint in hints.shown
                ],
                courts_total=hints.total,
            )
        )
    return BaseUnnamedListResponse(
        statuses=[
            OptionResponse(value=key, label=label, count=totals[key])
            for key, label in _STATUSES.items()
        ],
        items=items,
        total=len(chosen),
        page=page,
        page_size=PAGE_SIZE,
        none_yet=not cases,
    )


@router.post("/base-unnamed/decide", response_model=BaseUnnamedDecisionResponse)
def decide_base_unnamed(
    body: BaseUnnamedDecisionRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> BaseUnnamedDecisionResponse:
    """«Это он», «Не он» or «Отменить» («clear») of a candidate."""
    return BaseUnnamedDecisionResponse(record=apply_decide(db, body.model_dump()))

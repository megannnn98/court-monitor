"""«Безымянные» for the React console: the figurants a publication does not name, their
candidates, and the operator's words on them, read and written as the legacy page does
(`web.ui.unnamed`). The words are actions, refused from other origins (`web.csrf`)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from entities.base_candidates import base_candidates
from entities.jurisdiction import Jurisdiction
from entities.named_candidates import named_candidates, person_candidate_key
from entities.unnamed import EXISTING_PERSON, IDENTIFIED, RF_ENTRY, candidates
from web.dependencies import get_db
from web.response_models import (
    CourtHintResponse,
    OptionResponse,
    PersonChoiceResponse,
    UnnamedActionRequest,
    UnnamedActionResponse,
    UnnamedBaseCandidateResponse,
    UnnamedCardResponse,
    UnnamedListResponse,
    UnnamedNamedCandidateResponse,
    UnnamedReverseResponse,
    UnnamedRfCandidateResponse,
    UnnamedRfEntryResponse,
    UnnamedScreenedResponse,
)
from web.ui.entities import display_name
from web.ui.layout import external_url
from web.ui.unnamed import (
    _STATUSES,
    PAGE_SIZE,
    Resolution,
    _day,
    apply_clear,
    apply_keep,
    apply_reject,
    apply_resolve,
    facts_text,
    people_search,
    reverse_matches,
    rf_display_name,
    rf_entry_key,
    rf_search,
    screened,
    screened_reason,
    seen_text,
    unnamed_listing,
)

router = APIRouter()


def _note(resolution: Resolution | None) -> tuple[str | None, str | None]:
    """Who the figurant was identified as, and how: (name, note)."""
    if resolution is None or resolution[0] not in IDENTIFIED:
        return None, None
    kind, normalized_name, existing_key, rf_name, rf_birth_date, decided_at = resolution
    name = str(normalized_name or rf_name or existing_key or "")
    source = (
        f"запись РФМ {rf_name or ''}, {rf_birth_date:%d.%m.%Y}"
        if kind == RF_ENTRY and rf_birth_date
        else "существующий человек"
        if kind == EXISTING_PERSON
        else "имя указано вручную"
    )
    when = f", {_day(decided_at)}" if decided_at else ""
    return name, f"Опознан оператором: {source}{when}."


@router.get("/unnamed", response_model=UnnamedListResponse)
def list_unnamed(
    status: str = Query(default="open", pattern="^(open|no_age|found|no_rf|insufficient|all)$"),
    page: int = Query(default=1, ge=1),
    person_q: str = Query(default="", max_length=100),
    rf_q: str = Query(default="", max_length=100),
    rf_key: str = Query(default="", max_length=600),
    db: Session = Depends(get_db),  # noqa: B008
) -> UnnamedListResponse:
    """One tab of «Безымянные», as the legacy page shows it."""
    listing = unnamed_listing(db, status)
    on_page = listing.chosen[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    jurisdiction = Jurisdiction.from_session(db)
    cards = []
    for item in on_page:
        found = candidates(db, item)
        in_base = base_candidates(db, item)
        courts = jurisdiction.courts(item.place, item.articles)
        resolution = listing.words.get(item.key)
        name, note = _note(resolution)
        cards.append(
            UnnamedCardResponse(
                key=item.key,
                article_id=item.article_id,
                start_offset=item.start_offset,
                end_offset=item.end_offset,
                quote=item.quote,
                published_at=item.published_at,
                facts=facts_text(item),
                explanation=item.explanation,
                has_age=item.age is not None,
                resolution=resolution[0] if resolution else "",
                identified_as=name,
                note=note,
                rf_candidates=[
                    UnnamedRfCandidateResponse(
                        key=candidate.key,
                        full_name=candidate.full_name,
                        display_name=rf_display_name(candidate.full_name),
                        rf_name=candidate.key.split("|")[0],
                        birth_date=candidate.birth_date,
                        birth_place=candidate.birth_place,
                        reasons=candidate.reasons,
                        seen=seen_text(candidate),
                        decision=candidate.decision,
                    )
                    for candidate in found.shown
                ],
                rf_total=found.total,
                rf_snapshot_date=found.snapshot_date,
                named_candidates=[
                    UnnamedNamedCandidateResponse(
                        key=candidate.key,
                        candidate_key=person_candidate_key(candidate.key),
                        name=display_name(candidate.name),
                        article_id=candidate.article_id,
                        title=candidate.title,
                        start=candidate.start,
                        end=candidate.end,
                        reasons=candidate.reasons,
                        decision=candidate.decision,
                    )
                    for candidate in named_candidates(db, item)
                ],
                base_candidates=[
                    UnnamedBaseCandidateResponse(
                        key=candidate.key,
                        name=candidate.name,
                        birth_date=candidate.birth_date,
                        place=", ".join(
                            part for part in (candidate.region, candidate.city) if part
                        ),
                        articles=candidate.articles,
                        reasons=candidate.reasons,
                        decision=candidate.decision,
                    )
                    for candidate in in_base.shown
                ],
                base_total=in_base.total,
                courts=[
                    CourtHintResponse(
                        court=hint.court, cases=hint.cases, site=external_url(hint.site)
                    )
                    for hint in courts.shown
                ],
                courts_total=courts.total,
            )
        )
    entries = rf_search(db, rf_q)
    keys = {key for entry in entries if (key := rf_entry_key(entry)) is not None}
    chosen_key = rf_key if rf_key in keys else ""
    reverse = (
        reverse_matches(
            db, [item for item in listing.figurants if listing.state(item) == "open"], chosen_key
        )
        if chosen_key
        else []
    )
    return UnnamedListResponse(
        statuses=[
            OptionResponse(value=key, label=label, count=listing.counts[key])
            for key, label in _STATUSES.items()
        ],
        items=cards,
        total=len(listing.chosen),
        page=page,
        page_size=PAGE_SIZE,
        none_yet=not listing.figurants,
        people=[
            PersonChoiceResponse(key=person.key, name=person.name)
            for person in people_search(db, person_q)
        ],
        rf_entries=[
            UnnamedRfEntryResponse(
                key=rf_entry_key(entry),
                full_name=entry.full_name,
                birth_date=entry.birth_date.date() if entry.birth_date else None,
            )
            for entry in entries
        ],
        reverse=[
            UnnamedReverseResponse(key=item.key, quote=item.quote, facts=facts_text(item))
            for item in reverse
        ],
        screened=[
            UnnamedScreenedResponse(
                key=item.key,
                article_id=item.article_id,
                start_offset=item.start_offset,
                end_offset=item.end_offset,
                quote=item.quote,
                published_at=item.published_at,
                facts=facts_text(item),
                reason=screened_reason(item.reason),
                explanation=item.explanation,
            )
            for item in screened(db)
        ],
    )


def _fields(body: UnnamedActionRequest) -> dict[str, str]:
    """The request as the legacy form's fields: one validation for both."""
    fields = body.model_dump(exclude={"undo"})
    fields["undo"] = "1" if body.undo else ""
    return {name: str(value) for name, value in fields.items()}


@router.post("/unnamed/keep", response_model=UnnamedActionResponse)
def keep_unnamed(
    body: UnnamedActionRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> UnnamedActionResponse:
    """«Вернуть на разбор»: back to the cards, whom the search set aside."""
    return UnnamedActionResponse(figurant=apply_keep(db, _fields(body)))


@router.post("/unnamed/reject", response_model=UnnamedActionResponse)
def reject_unnamed(
    body: UnnamedActionRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> UnnamedActionResponse:
    """«Не он» of a candidate; with `undo`, taken back."""
    return UnnamedActionResponse(figurant=apply_reject(db, _fields(body)))


@router.post("/unnamed/resolve", response_model=UnnamedActionResponse)
def resolve_unnamed(
    body: UnnamedActionRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> UnnamedActionResponse:
    """«Это он», «Подходящей записи РФМ нет», «Недостаточно данных»."""
    return UnnamedActionResponse(figurant=apply_resolve(db, _fields(body)))


@router.post("/unnamed/clear", response_model=UnnamedActionResponse)
def clear_unnamed(
    body: UnnamedActionRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> UnnamedActionResponse:
    """«Отменить решение»."""
    return UnnamedActionResponse(figurant=apply_clear(db, _fields(body)))

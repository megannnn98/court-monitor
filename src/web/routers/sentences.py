"""«Приговоры» for the React console. Read-only: «Неверно» and «Вернуть» stay legacy forms
until mutations are authenticated (ADR 0022)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from entities.ask import KIND_LABELS, REASON_LABELS
from entities.sentence_cases import POLITICAL
from web.dependencies import get_db
from web.response_models import OptionResponse, SentenceListResponse, SentenceRowResponse
from web.ui.sentences import HIDDEN, PAGE_SIZE, read_sentences, term_text

router = APIRouter()


@router.get("/sentences", response_model=SentenceListResponse)
def list_sentences(
    reason: str = Query(default=POLITICAL, max_length=32),
    region: str = Query(default="", max_length=64),
    view: str = Query(default="", pattern=f"^({HIDDEN})?$"),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> SentenceListResponse:
    """The sentences a model read from the publications, folded into cases, newest first;
    `view=hidden` lists the rows a person took out of every count."""
    data = read_sentences(db, reason=reason, region=region, page=page)
    if view == HIDDEN:
        items = [
            SentenceRowResponse(
                row_ids=[row.id],
                person=row.person,
                region=row.region,
                kind_label=KIND_LABELS.get(row.kind, row.kind),
                in_absentia=bool(row.in_absentia),
                term=term_text(row.months, row.fine_rub),
                sentenced_on=row.sentenced_on,
                reason_label=REASON_LABELS.get(row.reason, row.reason),
                reason_text=row.reason_text,
                article_id=row.article_id,
                title=row.title,
                source=None,
                more_publications=0,
                quote=row.quote,
            )
            for row in data.hidden
        ]
        total = len(items)
    else:
        items = [
            SentenceRowResponse(
                row_ids=list(case.row_ids),
                person=case.person,
                region=case.region or None,
                kind_label=KIND_LABELS[case.kind],
                in_absentia=case.in_absentia,
                term=term_text(case.months, case.fine_rub),
                sentenced_on=case.sentenced_on or None,
                reason_label=REASON_LABELS[case.reason],
                reason_text=case.reason_text,
                article_id=case.article_id,
                title=case.title,
                source=case.source,
                more_publications=len(case.publications) - 1,
                quote=case.quote,
            )
            for case in data.cases
        ]
        total = data.total
    return SentenceListResponse(
        view=view,
        reason=data.reason,
        region=data.region,
        items=items,
        total=total,
        page=page if view != HIDDEN else 1,
        page_size=PAGE_SIZE if view != HIDDEN else max(total, 1),
        read=data.read,
        cases=len(data.all_cases),
        hidden=len(data.hidden),
        reasons=[
            OptionResponse(value=value, label=label)
            for value, label in (
                ("", "Все дела"),
                (POLITICAL, "Все политические"),
                *REASON_LABELS.items(),
            )
        ],
        regions=sorted({case.region for case in data.all_cases if case.region}),
    )

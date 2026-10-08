"""«Вики» and «Обзор» for the React console, read as the legacy pages read them
(`web.ui.wiki`, `web.ui.overview`). Reads only; the wiki's PDF stays a file of the legacy
route."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from entities.news import NEW_CASE, SENTENCE
from entities.unnamed import candidates
from web.dependencies import get_db
from web.response_models import (
    OverviewNewsResponse,
    OverviewResponse,
    OverviewUnnamedResponse,
    WikiPageLinkResponse,
    WikiPageResponse,
)
from web.ui.entities import display_name
from web.ui.management import recent_source_errors
from web.ui.overview import found_text, latest_news, news_counts, open_unnamed, short_quote
from web.ui.unnamed import facts_text
from web.ui.workload import workload
from web.wiki import _wiki_markdown_to_html, _wiki_page, _wiki_pages

router = APIRouter()


@router.get("/wiki", response_model=list[WikiPageLinkResponse])
def list_wiki() -> list[WikiPageLinkResponse]:
    return [WikiPageLinkResponse(slug=page.stem) for page in _wiki_pages()]


@router.get("/wiki/{slug}", response_model=WikiPageResponse, responses={404: {}})
def get_wiki_page(slug: str) -> WikiPageResponse:
    page = _wiki_page(slug)
    if page is None:
        raise HTTPException(status_code=404, detail="Wiki page not found")
    return WikiPageResponse(
        slug=page.stem,
        html=_wiki_markdown_to_html(page.read_text(encoding="utf-8"), page_link_prefix="/wiki/"),
    )


def _news(rows: list[Any]) -> list[OverviewNewsResponse]:
    return [
        OverviewNewsResponse(
            key=key, name=display_name(name), published_at=published_at, reason=reason
        )
        for key, name, published_at, reason in rows
    ]


@router.get("/overview", response_model=OverviewResponse)
def get_overview(db: Session = Depends(get_db)) -> OverviewResponse:  # noqa: B008
    work = workload(db)
    counts = news_counts(db)
    return OverviewResponse(
        source_errors=recent_source_errors(db),
        new_cases=counts.get(NEW_CASE, 0),
        latest_new_cases=_news(latest_news(db, NEW_CASE)),
        sentences=counts.get(SENTENCE, 0),
        latest_sentences=_news(latest_news(db, SENTENCE)),
        unnamed=work.unnamed,
        latest_unnamed=[
            OverviewUnnamedResponse(
                key=figurant.key,
                quote=short_quote(figurant.quote),
                published_at=figurant.published_at,
                facts=facts_text(figurant),
                found=found_text(candidates(db, figurant), age_told=figurant.age is not None),
            )
            for figurant in open_unnamed(db)
        ],
        pairs=work.pairs,
        unclear_roles=work.unclear_roles,
        unclear_verdicts=work.unclear_verdicts,
    )

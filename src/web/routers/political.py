"""«Результат» for the React console: the legacy list under its filters, a page of rows
and every count the filters show. Read-only: the operator's «обработано» stays a legacy
form until the authentication of mutations is decided."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from entities.news import KIND_LABELS, NEW_CASE, ONGOING, SENTENCE
from entities.rf_articles import AWAITED
from entities.rf_check import FULL
from entities.rf_entry import entry_included_text
from persecution.classifier import POLITICAL_ARTICLES
from web.dependencies import get_db
from web.exports import is_web_link
from web.response_models import (
    KnownResponse,
    OptionResponse,
    PoliticalArticleResponse,
    PoliticalListResponse,
    PoliticalRowResponse,
    PublicationLinkResponse,
)
from web.ui.political import PAGE_SIZE, _basis
from web.ui.political_filters import (
    KNOWN_FILTERS,
    NEWS_FILTERS,
    PERIODS,
    RFM_FILTERS,
    WHO_FILTERS,
    filters,
)
from web.ui.political_rows import ListRow, result_rows, with_details

router = APIRouter()


def _rf_label(row: ListRow) -> str | None:
    if row.rf_level == FULL:
        return "в перечне РФМ"
    return "возможно в перечне" if row.maybe_listed else None


def _row(row: ListRow, *, base_loaded: bool) -> PoliticalRowResponse:
    listing = row.listing
    match = row.known
    return PoliticalRowResponse(
        key=row.key,
        name=row.shown_name,
        url=row.href,
        unnamed=row.unnamed,
        done=row.done,
        news_kind=row.news_kind,
        news_label=KIND_LABELS.get(row.news_kind, row.news_kind) if row.news_kind else None,
        news_reason=row.news_reason,
        known=(
            KnownResponse(level=match.level, label=match.label, names=list(match.names))
            if base_loaded and match is not None
            else None
        ),
        not_in_base=base_loaded and match is None,
        regions=row.regions,
        rf_level=row.rf_level,
        rf_label=_rf_label(row),
        rf_entry=row.rf_entry,
        # Only for an entry matched with the patronymic: on a namesake the day is somebody
        # else's.
        rf_included=(
            entry_included_text(row.rf_inclusion_date, row.rf_inclusion_source)
            if row.rf_level == FULL and row.rf_inclusion_date
            else None
        ),
        listing=listing.text if listing else None,
        awaited=listing is not None and listing.kind == AWAITED,
        articles=[
            PoliticalArticleResponse(article=article, political=article in POLITICAL_ARTICLES)
            for article, _ in row.articles
        ],
        basis=_basis(row),
        basis_quote=row.basis.quote[:200],
        memorial=row.memorial,
        first_published_at=row.first_published,
        last_published_at=row.last_published,
        links=[
            PublicationLinkResponse(title=title[:80], url=url, source=source)
            for title, url, _, source in row.links
            if is_web_link(url)
        ],
    )


@router.get("/political", response_model=PoliticalListResponse)
def list_political(
    months: int = Query(default=0),
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    news: str = Query(default="all", max_length=16),
    known: str = Query(default="all", max_length=16),
    done: str = Query(default="hide", max_length=8),
    who: str = Query(default="all", max_length=8),
    rfm: str = Query(default="all", max_length=8),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> PoliticalListResponse:
    """The people with a political criminal case, latest news first, as the legacy
    «Результат» lists them. Dates (day/month/year or ISO) win over the months. Unlike the
    legacy page, filters are not remembered: the address is the state."""
    chosen = filters(months, date_from, date_to, news, known, done, who, rfm)
    result = result_rows(db, chosen, datetime.now(UTC))
    base_loaded = bool(result.base_size)
    on_page = with_details(db, result.rows[(page - 1) * PAGE_SIZE : page * PAGE_SIZE])
    return PoliticalListResponse(
        items=[_row(row, base_loaded=base_loaded) for row in on_page],
        total=len(result.rows),
        page=page,
        page_size=PAGE_SIZE,
        done_total=result.done_total,
        awaited=result.awaited,
        base_loaded=base_loaded,
        periods=[OptionResponse(value=str(key), label=label) for key, label in PERIODS.items()],
        # The rarer kinds are offered only when somebody has them, as on the legacy page.
        news=[
            OptionResponse(
                value=key,
                label=label,
                count=None if key == "all" else result.news_counts.get(key, 0),
            )
            for key, label in NEWS_FILTERS.items()
            if key in ("all", NEW_CASE, SENTENCE, ONGOING) or result.news_counts.get(key)
        ],
        known=(
            [
                OptionResponse(
                    value=key,
                    label=label,
                    count=None if key == "all" else result.known_counts.get(key, 0),
                )
                for key, label in KNOWN_FILTERS.items()
            ]
            if base_loaded
            else []
        ),
        who=[OptionResponse(value=key, label=label) for key, label in WHO_FILTERS.items()],
        rfm=[
            OptionResponse(
                value=key, label=label, count=result.awaited if key == "awaited" else None
            )
            for key, label in RFM_FILTERS.items()
        ],
    )

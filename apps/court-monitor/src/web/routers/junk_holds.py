"""«Отсев» for the React console: the articles the junk screen held back from the purge,
in stories, and a person's words on them, as the legacy page reads and takes them
(`web.ui.junk_holds`). The words are actions, refused from other origins (`web.csrf`)."""

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from web.dependencies import get_db
from web.response_models import (
    HeldActionResponse,
    HeldArticleRequest,
    HeldArticleResponse,
    HeldArticlesRequest,
    HeldStoryResponse,
    JunkHoldsResponse,
    OptionResponse,
)
from web.ui.junk_holds import (
    _STATUSES,
    apply_hold,
    apply_junk,
    apply_junk_all,
    apply_reextract,
    apply_release,
    apply_unrelease,
    default_status,
    held_stories,
    junk_counts,
    note_label,
)
from web.ui.layout import external_url

router = APIRouter()


def _article(row: Any) -> HeldArticleResponse:
    return HeldArticleResponse(
        article_id=row.article_id,
        status=row.status,
        title=row.title,
        published_at=row.published_at,
        source=row.source,
        url=external_url(row.canonical_url),
        score=row.score,
        cutoff=row.cutoff,
        events=row.events,
        start=" ".join((row.start or "").split()),
        reason=row.reason,
        note=row.note,
        note_label=note_label(row.reader_verdict) if row.note else "",
    )


@router.get("/junk-holds", response_model=JunkHoldsResponse)
def list_junk_holds(
    status: str | None = Query(default=None, pattern=f"^({'|'.join(_STATUSES)})$"),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> JunkHoldsResponse:
    """One list of «Отсев», as the legacy page shows it; without a status, the one with
    work in it."""
    counts = junk_counts(db)
    chosen = status or default_status(counts)
    by_id, pages = held_stories(db, chosen)
    return JunkHoldsResponse(
        status=chosen,
        statuses=[
            OptionResponse(value=key, label=label, count=counts.get(key, 0))
            for key, label in _STATUSES.items()
        ],
        stories=[
            HeldStoryResponse(articles=[_article(by_id[article]) for article in story])
            for story in (pages[page - 1] if page <= len(pages) else [])
        ],
        article_ids=list(by_id),
        page=page,
        pages=len(pages),
    )


@router.post("/junk-holds/junk", response_model=HeldActionResponse)
def junk_held(
    body: HeldArticleRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> HeldActionResponse:
    """«Мусор»: the next purge deletes it."""
    apply_junk(db, body.article)
    return HeldActionResponse(articles=[body.article])


@router.post("/junk-holds/junk-all", response_model=HeldActionResponse)
def junk_all_held(
    body: HeldArticlesRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> HeldActionResponse:
    """«Мусор — все»: every one, or none when one is no longer held."""
    apply_junk_all(db, body.articles)
    return HeldActionResponse(articles=body.articles)


@router.post("/junk-holds/release", response_model=HeldActionResponse)
def release_held(
    body: HeldArticleRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> HeldActionResponse:
    """«Это дело — в работу»."""
    apply_release(db, body.article)
    return HeldActionResponse(articles=[body.article])


@router.post("/junk-holds/unrelease", response_model=HeldActionResponse)
def unrelease_held(
    body: HeldArticleRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> HeldActionResponse:
    """«Мусор» of a released article: back under the purge."""
    apply_unrelease(db, body.article)
    return HeldActionResponse(articles=[body.article])


@router.post("/junk-holds/hold", response_model=HeldActionResponse)
def hold_held(
    body: HeldArticleRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> HeldActionResponse:
    """«Вернуть на проверку» of an article marked junk."""
    apply_hold(db, body.article)
    return HeldActionResponse(articles=[body.article])


@router.post("/junk-holds/reextract", response_model=HeldActionResponse)
def reextract_held(
    body: HeldArticleRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> HeldActionResponse:
    """«Извлечь заново»: an event found returns the article to work."""
    return HeldActionResponse(articles=[body.article], released=apply_reextract(db, body.article))

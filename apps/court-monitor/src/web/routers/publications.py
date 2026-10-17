"""«Публикации» for the React console, and the people and events of one article. Read-only."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.orm_models import ParsedArticleRecord
from web.dependencies import get_db
from web.response_models import (
    ArticleMentionsResponse,
    EventCountResponse,
    PersonLinkResponse,
    PublicationListResponse,
    PublicationRowResponse,
    SourceCountResponse,
)
from web.ui.entities import EVENT_LABELS, display_name
from web.ui.publications import PAGE_SIZE, PEOPLE_SHOWN, people_and_events, read_publications

router = APIRouter()


def _people(found: list[tuple[str, str, int]]) -> list[PersonLinkResponse]:
    """Most mentioned first, as the legacy pages order them."""
    return [
        PersonLinkResponse(
            key=key, name=display_name(name), dossier_url=f"/ui/investigations/{quote(key)}"
        )
        for key, name, _mentions in sorted(found, key=lambda item: -item[2])
    ]


def _events(found: list[tuple[str, int]]) -> list[EventCountResponse]:
    return [
        EventCountResponse(kind=kind, label=EVENT_LABELS.get(kind, kind), count=count)
        for kind, count in sorted(found)
    ]


@router.get("/publications", response_model=PublicationListResponse)
def list_publications(
    q: str = Query(default="", max_length=200),
    source: int = Query(default=0, ge=0),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> PublicationListResponse:
    """The publications with a criminal case, newest first, searched by title and text and
    filtered by source; each with its people and events."""
    data = read_publications(db, q=q, source=source, page=page)
    items = []
    for row in data.rows:
        people = _people(data.people.get(row.id, []))
        items.append(
            PublicationRowResponse(
                id=row.id,
                title=row.title,
                published_at=row.published_at,
                source=row.source,
                url=row.canonical_url,
                people=people[:PEOPLE_SHOWN],
                more_people=max(0, len(people) - PEOPLE_SHOWN),
                events=_events(data.events.get(row.id, [])),
            )
        )
    return PublicationListResponse(
        items=items,
        total=data.total,
        page=page,
        page_size=PAGE_SIZE,
        sources=[
            SourceCountResponse(id=source_id, name=name, count=count)
            for source_id, name, count in data.sources
        ],
    )


@router.get("/articles/{article_id}/mentions", response_model=ArticleMentionsResponse)
def get_article_mentions(
    article_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> ArticleMentionsResponse:
    """The people an article names and the events found in it, as the legacy article
    page shows them above the text."""
    if db.get(ParsedArticleRecord, article_id) is None:
        raise HTTPException(status_code=404, detail="Article not found")
    people, events = people_and_events(db, [article_id])
    return ArticleMentionsResponse(
        people=_people(people.get(article_id, [])), events=_events(events.get(article_id, []))
    )

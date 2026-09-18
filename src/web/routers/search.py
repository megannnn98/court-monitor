"""Lexical article search."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from search.postgres_lexical import PostgresLexicalSearch
from sources.models import SearchHit, SearchQuery
from web.dependencies import get_db, session_factory_for

router = APIRouter()


@router.get("/search/articles", response_model=list[SearchHit])
def search_articles(
    query: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[SearchHit]:
    """HTTP route over the existing PostgreSQL lexical search backend."""
    session_factory = session_factory_for(db)
    return PostgresLexicalSearch(session_factory).search(SearchQuery(text=query, limit=limit))

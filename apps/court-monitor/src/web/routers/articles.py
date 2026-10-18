"""Parsed articles."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ingestion.read import article_detail
from web.dependencies import get_db
from web.response_models import ArticleResponse

router = APIRouter()


def _article_response(db: Session, article_id: int) -> ArticleResponse:
    article = article_detail(db, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return ArticleResponse(
        id=article.id,
        source_name=article.source_name,
        external_id=article.external_id,
        title=article.title,
        published_at=article.published_at.isoformat() if article.published_at else None,
        url=article.url,
        text=article.text,
    )


@router.get("/articles/{article_id}", response_model=ArticleResponse)
def get_article(
    article_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> ArticleResponse:
    """Full ParsedArticle text for evidence inspection."""
    return _article_response(db, article_id)

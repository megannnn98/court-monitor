"""Parsed articles."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import (
    ParsedArticleRecord,
    Source,
    SourceDocument,
)
from web.dependencies import get_db
from web.response_models import ArticleResponse

router = APIRouter()


def _article_response(db: Session, article_id: int) -> ArticleResponse:
    row = db.execute(
        select(ParsedArticleRecord, SourceDocument, Source)
        .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
        .join(Source, Source.id == SourceDocument.source_id)
        .where(ParsedArticleRecord.id == article_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Article not found")
    article, document, source = row
    return ArticleResponse(
        id=article.id,
        source_name=source.name,
        external_id=document.external_id,
        title=article.title,
        published_at=article.published_at.isoformat() if article.published_at else None,
        url=document.canonical_url,
        text=article.text,
    )


@router.get("/articles/{article_id}", response_model=ArticleResponse)
def get_article(
    article_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> ArticleResponse:
    """Full ParsedArticle text for evidence inspection."""
    return _article_response(db, article_id)

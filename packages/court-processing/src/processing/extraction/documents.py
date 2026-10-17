from __future__ import annotations

from hashlib import sha256

from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session, sessionmaker

from ingestion import read
from monitor_core.model import ParsedArticle
from processing.extraction.models import ExtractionDocument


def content_hash_for_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def build_extraction_document_from_parsed_article(
    *,
    article_id: int,
    article: ParsedArticle,
    source_name: str,
    source_url: str,
) -> ExtractionDocument:
    return ExtractionDocument(
        article_id=article_id,
        title=article.title,
        text=article.text,
        published_at=article.published_at,
        source_name=source_name,
        source_url=source_url,
        content_hash=content_hash_for_text(article.text),
    )


class SqlAlchemyExtractionDocumentRepository:
    """The articles extraction reads, through the ingestion's read API."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_by_article_id(self, article_id: int) -> ExtractionDocument:
        with self._session_factory() as session:
            found = read.stored_articles(session, [article_id])
        if article_id not in found:
            raise NoResultFound(f"no stored article {article_id}")
        return _document(found[article_id])

    def list_documents(
        self,
        *,
        source_name: str | None,
        limit: int,
    ) -> list[ExtractionDocument]:
        with self._session_factory() as session:
            articles = read.list_stored_articles(session, source_name=source_name, limit=limit)
        return [_document(article) for article in articles]


def _document(article: read.StoredArticle) -> ExtractionDocument:
    return ExtractionDocument(
        article_id=article.id,
        title=article.title,
        text=article.text,
        published_at=article.published_at,
        source_name=article.source_name,
        source_url=article.url,
        content_hash=content_hash_for_text(article.text),
    )

from __future__ import annotations

from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from extraction_models import ExtractionDocument
from models import ParsedArticle
from orm_models import ParsedArticleRecord, Source, SourceDocument


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
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_by_article_id(self, article_id: int) -> ExtractionDocument:
        with self._session_factory() as session:
            row = session.execute(
                select(ParsedArticleRecord, Source, SourceDocument)
                .join(SourceDocument, ParsedArticleRecord.document_id == SourceDocument.id)
                .join(Source, SourceDocument.source_id == Source.id)
                .where(ParsedArticleRecord.id == article_id)
            ).one()
            article, source, document = row
            return ExtractionDocument(
                article_id=article.id,
                title=article.title,
                text=article.text,
                published_at=article.published_at,
                source_name=source.name,
                source_url=document.canonical_url,
                content_hash=content_hash_for_text(article.text),
            )

    def list_documents(
        self,
        *,
        source_name: str | None,
        limit: int,
    ) -> list[ExtractionDocument]:
        with self._session_factory() as session:
            statement = (
                select(ParsedArticleRecord, Source, SourceDocument)
                .join(SourceDocument, ParsedArticleRecord.document_id == SourceDocument.id)
                .join(Source, SourceDocument.source_id == Source.id)
                .order_by(ParsedArticleRecord.id)
                .limit(limit)
            )
            if source_name is not None:
                statement = statement.where(Source.name == source_name)
            rows = session.execute(statement).all()
            return [
                ExtractionDocument(
                    article_id=article.id,
                    title=article.title,
                    text=article.text,
                    published_at=article.published_at,
                    source_name=source.name,
                    source_url=document.canonical_url,
                    content_hash=content_hash_for_text(article.text),
                )
                for article, source, document in rows
            ]

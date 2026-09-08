from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from models import ArticleChunk, ParsedArticle, PersistenceResult, RawDocument
from orm_models import (
    ArticleChunkRecord,
    ParsedArticleRecord,
    Source,
    SourceDocument,
)

"""
Сохраняет результат обработки одной публикации в PostgreSQL.
Он получает исходный документ, распарсенную статью и её chunks,
а затем согласованно записывает их в несколько связанных таблиц.

RawDocument + ParsedArticle + ArticleChunk[]
                    ↓
      SqlAlchemyIngestionPersistence.save()
                    ↓
 sources → source_documents → parsed_articles → article_chunks

"""


class SqlAlchemyIngestionPersistence:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        source_name: str,
        source_base_url: str,
    ) -> None:
        self._session_factory = session_factory
        self._source_name = source_name
        self._source_base_url = source_base_url

    def _get_or_create_source(self, session: Session) -> Source:
        source = session.scalar(select(Source).where(Source.base_url == self._source_base_url))

        if source is not None:
            return source

        source = Source(
            name=self._source_name,
            base_url=self._source_base_url,
        )
        session.add(source)
        session.flush()

        return source

    def _get_or_create_document(
        self,
        session: Session,
        source: Source,
        raw_document: RawDocument,
    ) -> SourceDocument:
        document = session.scalar(
            select(SourceDocument).where(
                SourceDocument.source_id == source.id,
                SourceDocument.external_id == raw_document.external_id,
            )
        )

        if document is not None:
            document.canonical_url = raw_document.url
            document.fetched_at = raw_document.fetched_at
            document.content_type = raw_document.content_type
            document.raw_content = raw_document.content
            return document

        document = SourceDocument(
            source_id=source.id,
            external_id=raw_document.external_id,
            canonical_url=raw_document.url,
            fetched_at=raw_document.fetched_at,
            content_type=raw_document.content_type,
            raw_content=raw_document.content,
        )
        session.add(document)
        session.flush()

        return document

    @staticmethod
    def _get_or_create_parsed_article(
        session: Session,
        document: SourceDocument,
        article: ParsedArticle,
    ) -> ParsedArticleRecord:
        parsed_article = session.scalar(
            select(ParsedArticleRecord).where(ParsedArticleRecord.document_id == document.id)
        )

        if parsed_article is not None:
            parsed_article.title = article.title
            parsed_article.published_at = article.published_at
            parsed_article.text = article.text
            return parsed_article

        parsed_article = ParsedArticleRecord(
            document_id=document.id,
            title=article.title,
            published_at=article.published_at,
            text=article.text,
        )
        session.add(parsed_article)
        session.flush()

        return parsed_article

    @staticmethod
    def _replace_chunks(
        session: Session,
        parsed_article: ParsedArticleRecord,
        chunks: Sequence[ArticleChunk],
    ) -> int:
        session.execute(
            delete(ArticleChunkRecord).where(
                ArticleChunkRecord.parsed_article_id == parsed_article.id
            )
        )
        records = [
            ArticleChunkRecord(
                parsed_article_id=parsed_article.id,
                ordinal=chunk.ordinal,
                text=chunk.text,
            )
            for chunk in chunks
        ]

        session.add_all(records)
        session.flush()

        return len(records)

    def save(
        self,
        raw_document: RawDocument,
        article: ParsedArticle,
        chunks: Sequence[ArticleChunk],
    ) -> PersistenceResult:
        # Сначала открывается транзакция
        with self._session_factory.begin() as session:
            # Находит источник по base_url и создает его, если его нет
            source = self._get_or_create_source(session)

            # Ищет документ по паре source_id и external_id
            document = self._get_or_create_document(
                session,
                source,
                raw_document,
            )

            # Ищет распарсенную статью, связанную с документом и создает ее, если ее нет
            parsed_article = self._get_or_create_parsed_article(
                session,
                document,
                article,
            )

            # Удаляет все старые chunks статьи и заменяет их новыми
            chunks_saved = self._replace_chunks(
                session,
                parsed_article,
                chunks,
            )

            return PersistenceResult(
                document_id=document.id,
                chunks_saved=chunks_saved,
            )

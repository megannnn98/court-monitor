from collections.abc import Sequence
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from models import ArticleChunk, ParsedArticle, PersistenceResult, RawDocument
from orm_models import (
    ArticleChunkRecord,
    DocumentSnapshot,
    ParsedArticleRecord,
    Source,
    SourceDocument,
)


def _calculate_content_hash(content: bytes) -> str:
    return sha256(content).hexdigest()


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
            return document

        document = SourceDocument(
            source_id=source.id,
            external_id=raw_document.external_id,
            canonical_url=raw_document.url,
        )
        session.add(document)
        session.flush()

        return document

    @staticmethod
    def _find_snapshot(
        session: Session,
        document: SourceDocument,
        content_hash: str,
    ) -> DocumentSnapshot | None:
        return session.scalar(
            select(DocumentSnapshot).where(
                DocumentSnapshot.document_id == document.id,
                DocumentSnapshot.content_hash == content_hash,
            )
        )

    @staticmethod
    def _create_snapshot(
        session: Session,
        document: SourceDocument,
        raw_document: RawDocument,
        content_hash: str,
    ) -> DocumentSnapshot:
        snapshot = DocumentSnapshot(
            document_id=document.id,
            fetched_at=raw_document.fetched_at,
            content_type=raw_document.content_type,
            raw_content=raw_document.content,
            content_hash=content_hash,
        )
        session.add(snapshot)
        session.flush()

        return snapshot

    @staticmethod
    def _create_parsed_article(
        session: Session,
        snapshot: DocumentSnapshot,
        article: ParsedArticle,
    ) -> ParsedArticleRecord:
        record = ParsedArticleRecord(
            snapshot_id=snapshot.id,
            title=article.title,
            published_at=article.published_at,
            text=article.text,
        )
        session.add(record)
        session.flush()

        return record

    @staticmethod
    def _create_chunks(
        session: Session,
        parsed_article: ParsedArticleRecord,
        chunks: Sequence[ArticleChunk],
    ) -> int:
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
        content_hash = _calculate_content_hash(raw_document.content)

        with self._session_factory.begin() as session:
            source = self._get_or_create_source(session)
            document = self._get_or_create_document(
                session,
                source,
                raw_document,
            )

            existing_snapshot = self._find_snapshot(
                session,
                document,
                content_hash,
            )

            if existing_snapshot is not None:
                return PersistenceResult(
                    document_id=document.id,
                    snapshot_id=existing_snapshot.id,
                    chunks_saved=0,
                    created_new_snapshot=False,
                )

            snapshot = self._create_snapshot(
                session,
                document,
                raw_document,
                content_hash,
            )
            parsed_article = self._create_parsed_article(
                session,
                snapshot,
                article,
            )
            chunks_saved = self._create_chunks(
                session,
                parsed_article,
                chunks,
            )

            return PersistenceResult(
                document_id=document.id,
                snapshot_id=snapshot.id,
                chunks_saved=chunks_saved,
                created_new_snapshot=True,
            )

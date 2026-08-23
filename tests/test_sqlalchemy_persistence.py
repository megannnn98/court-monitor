from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from models import ArticleChunk, ParsedArticle, RawDocument
from orm_models import (
    ArticleChunkRecord,
    DocumentSnapshot,
    ParsedArticleRecord,
    Source,
    SourceDocument,
)
from sqlalchemy_persistence import SqlAlchemyIngestionPersistence


def test_save_deduplicates_and_preserves_versions(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    )

    raw_v1 = RawDocument(
        external_id="article-1",
        url="https://ovd.info/article-1",
        fetched_at=datetime(2026, 8, 23, 8, 0, tzinfo=UTC),
        content_type="text/html",
        content=b"<html>version one</html>",
    )
    article_v1 = ParsedArticle(
        external_id="article-1",
        url=raw_v1.url,
        title="Version one",
        published_at=None,
        text="First paragraph\n\nSecond paragraph",
    )
    chunks_v1 = [
        ArticleChunk(ordinal=0, text="First paragraph"),
        ArticleChunk(ordinal=1, text="Second paragraph"),
    ]

    first = persistence.save(raw_v1, article_v1, chunks_v1)
    duplicate = persistence.save(raw_v1, article_v1, chunks_v1)

    raw_v2 = RawDocument(
        external_id="article-1",
        url="https://ovd.info/article-1",
        fetched_at=datetime(2026, 8, 23, 9, 0, tzinfo=UTC),
        content_type="text/html",
        content=b"<html>version two</html>",
    )
    article_v2 = ParsedArticle(
        external_id="article-1",
        url=raw_v2.url,
        title="Version two",
        published_at=None,
        text="Updated first\n\nUpdated second\n\nNew third",
    )
    chunks_v2 = [
        ArticleChunk(ordinal=0, text="Updated first"),
        ArticleChunk(ordinal=1, text="Updated second"),
        ArticleChunk(ordinal=2, text="New third"),
    ]

    changed = persistence.save(raw_v2, article_v2, chunks_v2)

    assert first.created_new_snapshot is True
    assert first.chunks_saved == 2

    assert duplicate.document_id == first.document_id
    assert duplicate.snapshot_id == first.snapshot_id
    assert duplicate.created_new_snapshot is False
    assert duplicate.chunks_saved == 0

    assert changed.document_id == first.document_id
    assert changed.snapshot_id != first.snapshot_id
    assert changed.created_new_snapshot is True
    assert changed.chunks_saved == 3

    with session_factory() as session:
        sources = session.scalars(select(Source)).all()
        documents = session.scalars(select(SourceDocument)).all()
        snapshots = session.scalars(select(DocumentSnapshot).order_by(DocumentSnapshot.id)).all()
        articles = session.scalars(
            select(ParsedArticleRecord).order_by(ParsedArticleRecord.id)
        ).all()
        chunks = session.scalars(
            select(ArticleChunkRecord).order_by(
                ArticleChunkRecord.parsed_article_id,
                ArticleChunkRecord.ordinal,
            )
        ).all()

        assert len(sources) == 1
        assert len(documents) == 1
        assert len(snapshots) == 2
        assert len(articles) == 2
        assert len(chunks) == 5

        assert [snapshot.raw_content for snapshot in snapshots] == [
            raw_v1.content,
            raw_v2.content,
        ]
        assert [article.title for article in articles] == [
            "Version one",
            "Version two",
        ]
        assert [chunk.ordinal for chunk in chunks] == [0, 1, 0, 1, 2]


def test_save_rolls_back_entire_transaction(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    )

    raw_document = RawDocument(
        external_id="rollback-test",
        url="https://ovd.info/rollback-test",
        fetched_at=datetime(2026, 8, 23, 10, 0, tzinfo=UTC),
        content_type="text/html",
        content=b"<html>rollback test</html>",
    )
    article = ParsedArticle(
        external_id=raw_document.external_id,
        url=raw_document.url,
        title="Rollback test",
        published_at=None,
        text="First\n\nSecond",
    )

    invalid_chunks = [
        ArticleChunk(ordinal=0, text="First"),
        ArticleChunk(ordinal=0, text="Duplicate ordinal"),
    ]

    with pytest.raises(IntegrityError):
        persistence.save(
            raw_document,
            article,
            invalid_chunks,
        )

    with session_factory() as session:
        assert session.scalars(select(Source)).all() == []
        assert session.scalars(select(SourceDocument)).all() == []
        assert session.scalars(select(DocumentSnapshot)).all() == []
        assert session.scalars(select(ParsedArticleRecord)).all() == []
        assert session.scalars(select(ArticleChunkRecord)).all() == []

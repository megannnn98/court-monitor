from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from models import ParsedArticle, RawDocument
from orm_models import ParsedArticleRecord, Source, SourceDocument
from sqlalchemy_persistence import SqlAlchemyIngestionPersistence


def _create_persistence(
    session_factory: sessionmaker[Session],
) -> SqlAlchemyIngestionPersistence:
    return SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    )


def test_save_does_not_update_existing_document(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = _create_persistence(session_factory)
    raw_v1 = RawDocument(
        external_id="article-1",
        url="https://ovd.info/article-1",
        fetched_at=datetime(2026, 8, 23, 8, 0, tzinfo=UTC),
        content_type="text/html",
        content=b"<html>version one</html>",
    )
    article_v1 = ParsedArticle(
        external_id=raw_v1.external_id,
        url=raw_v1.url,
        title="Version one",
        published_at=None,
        text="First paragraph\n\nSecond paragraph",
    )

    first = persistence.save(raw_v1, article_v1)

    raw_v2 = RawDocument(
        external_id=raw_v1.external_id,
        url="https://ovd.info/article-1-updated",
        fetched_at=datetime(2026, 8, 23, 9, 0, tzinfo=UTC),
        content_type="text/html; charset=UTF-8",
        content=b"<html>version two</html>",
    )
    article_v2 = ParsedArticle(
        external_id=raw_v2.external_id,
        url=raw_v2.url,
        title="Version two",
        published_at=datetime(2026, 8, 23, 9, 30, tzinfo=UTC),
        text="Updated first\n\nUpdated second\n\nNew third",
    )

    updated = persistence.save(raw_v2, article_v2)

    assert updated.document_id == first.document_id

    with session_factory() as session:
        sources = session.scalars(select(Source)).all()
        documents = session.scalars(select(SourceDocument)).all()
        articles = session.scalars(select(ParsedArticleRecord)).all()

        assert len(sources) == 1
        assert len(documents) == 1
        assert len(articles) == 1
        assert documents[0].canonical_url == raw_v2.url
        assert documents[0].fetched_at == raw_v2.fetched_at
        assert documents[0].content_type == raw_v2.content_type
        assert documents[0].raw_content == raw_v2.content
        assert articles[0].document_id == documents[0].id
        assert articles[0].title == article_v2.title
        assert articles[0].published_at == article_v2.published_at
        assert articles[0].text == article_v2.text


def test_parsed_article_document_id_is_unique(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = _create_persistence(session_factory)
    raw_document = RawDocument(
        external_id="article-unique",
        url="https://ovd.info/article-unique",
        fetched_at=datetime(2026, 8, 23, 8, 0, tzinfo=UTC),
        content_type="text/html",
        content=b"<html>unique</html>",
    )
    article = ParsedArticle(
        external_id=raw_document.external_id,
        url=raw_document.url,
        title="Unique",
        published_at=None,
        text="Text",
    )

    persistence.save(raw_document, article)

    with pytest.raises(IntegrityError), session_factory.begin() as session:
        document = session.scalar(select(SourceDocument))
        assert document is not None
        session.add(
            ParsedArticleRecord(
                document_id=document.id,
                title="Duplicate",
                published_at=None,
                text="Duplicate",
            )
        )
        session.flush()


def test_save_rolls_back_existing_document_update(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence = _create_persistence(session_factory)
    raw_v1 = RawDocument(
        external_id="rollback-test",
        url="https://ovd.info/rollback-test",
        fetched_at=datetime(2026, 8, 23, 10, 0, tzinfo=UTC),
        content_type="text/html",
        content=b"<html>original</html>",
    )
    article_v1 = ParsedArticle(
        external_id=raw_v1.external_id,
        url=raw_v1.url,
        title="Original",
        published_at=datetime(2026, 8, 23, 10, 30, tzinfo=UTC),
        text="Original first\n\nOriginal second",
    )
    persistence.save(raw_v1, article_v1)

    raw_v2 = RawDocument(
        external_id=raw_v1.external_id,
        url="https://ovd.info/rollback-test-updated",
        fetched_at=datetime(2026, 8, 23, 11, 0, tzinfo=UTC),
        content_type="application/xhtml+xml",
        content=b"<html>updated</html>",
    )
    article_v2 = ParsedArticle(
        external_id=raw_v2.external_id,
        url=raw_v2.url,
        title="Updated",
        published_at=datetime(2026, 8, 23, 11, 30, tzinfo=UTC),
        text="Updated first\n\nUpdated second",
    )

    original_get_or_create_parsed_article = (
        SqlAlchemyIngestionPersistence._get_or_create_parsed_article
    )

    def _fail_after_document_update(
        session: Session,
        document: SourceDocument,
        article: ParsedArticle,
    ) -> ParsedArticleRecord:
        original_get_or_create_parsed_article(session, document, article)
        raise RuntimeError("simulated failure while saving the parsed article")

    monkeypatch.setattr(
        SqlAlchemyIngestionPersistence,
        "_get_or_create_parsed_article",
        staticmethod(_fail_after_document_update),
    )

    with pytest.raises(RuntimeError, match="simulated failure"):
        persistence.save(raw_v2, article_v2)

    with session_factory() as session:
        document = session.scalar(select(SourceDocument))
        parsed_article = session.scalar(select(ParsedArticleRecord))

        assert document is not None
        assert parsed_article is not None
        assert document.canonical_url == raw_v1.url
        assert document.fetched_at == raw_v1.fetched_at
        assert document.content_type == raw_v1.content_type
        assert document.raw_content == raw_v1.content
        assert parsed_article.title == article_v1.title
        assert parsed_article.published_at == article_v1.published_at
        assert parsed_article.text == article_v1.text

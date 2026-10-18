"""Portable checks of the article read/command contracts.

PostgreSQL integration tests cover FK cascades and the pipeline. These tests exercise
the public boundary and caller-owned transactions without requiring a server.
"""

from collections.abc import Iterator
from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from ingestion.commands import discard_articles
from ingestion.read import article_count, article_detail, article_exists, stored_articles


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE sources (id INTEGER PRIMARY KEY, name TEXT, base_url TEXT)")
        )
        connection.execute(
            text(
                "CREATE TABLE source_documents (id INTEGER PRIMARY KEY, source_id INTEGER, external_id TEXT, canonical_url TEXT, raw_content TEXT, content_type TEXT)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE parsed_articles (id INTEGER PRIMARY KEY, document_id INTEGER, title TEXT, text TEXT, published_at DATETIME)"
            )
        )
        connection.execute(text("INSERT INTO sources VALUES (1, 'news', 'https://news.example')"))
        connection.execute(
            text(
                "INSERT INTO source_documents VALUES (11, 1, 'post-1', 'https://news.example/1', 'original-1', 'text/html'), (12, 1, 'post-2', 'https://news.example/2', 'original-2', 'text/html')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO parsed_articles VALUES (21, 11, 'One', 'first text', NULL), (22, 12, 'Two', 'second text', NULL)"
            )
        )
    try:
        with Session(engine) as opened:
            yield opened
    finally:
        engine.dispose()


def test_article_details_are_values_and_need_one_query(session: Session) -> None:
    calls: list[str] = []
    engine = session.get_bind()

    def count(*args: object) -> None:
        calls.append("query")

    event.listen(engine, "before_cursor_execute", count)
    article = article_detail(session, 21)

    assert len(calls) == 1
    assert article is not None
    assert article.text == "first text"
    assert article.url == "https://news.example/1"
    with pytest.raises(FrozenInstanceError):
        article.title = "changed"  # type: ignore[misc]  # Deliberately exercise runtime immutability.
    calls.clear()
    assert stored_articles(session, []) == {}
    assert calls == []


def test_detail_preserves_provenance_and_missing_article(session: Session) -> None:
    article = article_detail(session, 21)
    assert article is not None
    assert (article.external_id, article.source_name, article.title) == ("post-1", "news", "One")
    assert article_detail(session, 999) is None
    assert not article_exists(session, 999)


@pytest.mark.parametrize("content_type", [None, "application/x-court-monitor-expired"])
def test_discard_leaves_tombstones_and_respects_the_callers_rollback(
    session: Session, content_type: str | None
) -> None:
    discard_articles(session, [21], content_type=content_type)
    assert not article_exists(session, 21)
    assert article_count(session) == 1
    documents = session.execute(
        text("SELECT id, raw_content, content_type FROM source_documents ORDER BY id")
    ).all()
    assert tuple(documents[0]) == (11, "", content_type or "text/html")
    assert tuple(documents[1]) == (12, "original-2", "text/html")
    session.rollback()
    assert article_exists(session, 21)
    assert article_count(session) == 2

from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from models import ArticleChunk, ParsedArticle, RawDocument, SearchQuery
from postgres_lexical_search import PostgresLexicalSearch
from sqlalchemy_persistence import SqlAlchemyIngestionPersistence


def test_search_finds_russian_word_form(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    )

    raw_document = RawDocument(
        external_id="search-article",
        url="https://ovd.info/search-article",
        fetched_at=datetime(2026, 8, 13, 11, 0, tzinfo=UTC),
        content_type="text/html",
        content=b"<html>search article</html>",
    )

    chunks = [
        ArticleChunk(
            ordinal=0,
            text="Дело возбудили по статье о реабилитации нацизма.",
        ),
        ArticleChunk(
            ordinal=1,
            text="Совершенно нерелевантный текст о погоде.",
        ),
    ]

    published_at = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    article = ParsedArticle(
        external_id=raw_document.external_id,
        url=raw_document.url,
        title="Дело о реабилитации нацизма",
        published_at=published_at,
        text="\n\n".join(chunk.text for chunk in chunks),
    )

    persistence.save(raw_document, article, chunks)

    search = PostgresLexicalSearch(session_factory)
    hits = search.search(SearchQuery(text="реабилитация нацизма"))

    assert len(hits) == 1

    hit = hits[0]

    assert hit.title == "Дело о реабилитации нацизма"
    assert hit.url == raw_document.url
    assert hit.text == chunks[0].text
    assert hit.published_at == published_at
    assert hit.score > 0
    assert all(chunk.text != chunks[1].text for chunk in hits)


def test_search_respects_limit(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    )

    for index in range(2):
        raw_document = RawDocument(
            external_id=f"limit-article-{index}",
            url=f"https://ovd.info/limit-article-{index}",
            fetched_at=datetime(2026, 8, 13, 11, index, tzinfo=UTC),
            content_type="text/html",
            content=b"<html>article</html>",
        )
        chunk = ArticleChunk(
            ordinal=0,
            text="Дело возбудили по статье о реабилитации нацизма.",
        )
        article = ParsedArticle(
            external_id=raw_document.external_id,
            url=raw_document.url,
            title=f"Статья {index}",
            published_at=datetime(2026, 8, 13, 12, index, tzinfo=UTC),
            text=chunk.text,
        )

        persistence.save(raw_document, article, [chunk])

    search = PostgresLexicalSearch(session_factory)

    hits = search.search(
        SearchQuery(
            text="реабилитация нацизма",
            limit=1,
        )
    )

    assert len(hits) == 1
    assert hits[0].title == "Статья 0"


def test_search_orders_hits_by_relevance(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    )

    texts = [
        "Суд рассмотрел дело о реабилитации нацизма.",
        (
            "Реабилитация нацизма стала основанием для дела. "
            "Обвинение связано с реабилитацией нацизма."
        ),
    ]

    for index, text in enumerate(texts):
        raw_document = RawDocument(
            external_id=f"ranking-article-{index}",
            url=f"https://ovd.info/ranking-article-{index}",
            fetched_at=datetime(2026, 8, 13, 11, index, tzinfo=UTC),
            content_type="text/html",
            content=b"<html>article</html>",
        )
        article = ParsedArticle(
            external_id=raw_document.external_id,
            url=raw_document.url,
            title=f"Статья {index}",
            published_at=None,
            text=text,
        )

        persistence.save(
            raw_document,
            article,
            [ArticleChunk(ordinal=0, text=text)],
        )

    search = PostgresLexicalSearch(session_factory)
    hits = search.search(SearchQuery(text="реабилитация нацизма"))

    assert len(hits) == 2
    assert hits[0].title == "Статья 1"
    assert hits[0].score > hits[1].score

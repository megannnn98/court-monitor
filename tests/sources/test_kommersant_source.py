"""Kommersant's site through its RSS feed: filtered discovery and the article page."""

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from sources.ingestion_errors import ParseError, PermanentDiscoveryError
from sources.kommersant.article_parser import (
    KOMMERSANT_FEED_URL,
    KommersantArticleParser,
    is_case_news,
    kommersant_external_id,
)
from sources.models import RawDocument, SourceReference
from sources.rss.source_adapter import RssSourceAdapter, parse_rss
from sources.source_registry import get_source_definition

FIXTURES = Path(__file__).parents[1] / "fixtures" / "kommersant"
ARTICLE_HTML = (FIXTURES / "doc_8940075.html").read_bytes()


def _item(doc_id: int, category: str, title: str, description: str = "") -> str:
    return (
        f"<item><guid>https://www.kommersant.ru/doc/{doc_id}</guid>"
        f"<category>{category}</category><title>{title}</title>"
        f"<link>https://www.kommersant.ru/doc/{doc_id}</link>"
        "<pubDate>Wed, 09 Sep 2026 16:38:19 +0300</pubDate>"
        f"<description>{description}</description></item>"
    )


FEED = (
    '<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>'
    + _item(3, "Происшествия", "Крымчанин получил 21 год колонии за госизмену")
    + _item(2, "Мир", "The Times: король Карл отреагировал на обвинения")
    + _item(1, "Новости", "Курс рубля вырос", "Банк России установил курс.")
    + _item(0, "Общество", "Врача отправили в СИЗО", "Суд арестовал врача.")
    + "</channel></rss>"
).encode()


class FailingFetcher:
    async def fetch(self, reference: SourceReference) -> RawDocument:
        raise AssertionError("discovery must not fetch pages")


def _discover(feed: bytes, *, limit: int) -> list[SourceReference]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == KOMMERSANT_FEED_URL
        return httpx.Response(200, content=feed)

    async def run() -> list[SourceReference]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = RssSourceAdapter(
                client=client,
                feed_url=KOMMERSANT_FEED_URL,
                document_fetcher=FailingFetcher(),
                include=is_case_news,
                external_id=kommersant_external_id,
                base_delay_seconds=0,
            )
            return await adapter.discover(limit=limit)

    return asyncio.run(run())


def test_discovery_keeps_only_case_news_outside_the_skipped_sections() -> None:
    references = _discover(FEED, limit=10)

    assert references == [
        SourceReference(external_id="3", url="https://www.kommersant.ru/doc/3"),
        SourceReference(external_id="0", url="https://www.kommersant.ru/doc/0"),
    ]


def test_discovery_stops_at_the_limit() -> None:
    assert [reference.external_id for reference in _discover(FEED, limit=1)] == ["3"]


def test_an_unreadable_feed_is_a_permanent_discovery_error() -> None:
    with pytest.raises(PermanentDiscoveryError):
        _discover(b"<html>maintenance</html", limit=1)


def test_feed_items_carry_their_date() -> None:
    items = parse_rss(FEED)

    assert items[0].published_at == datetime(
        2026, 9, 9, 16, 38, 19, tzinfo=timezone(timedelta(hours=3))
    )
    assert items[0].category == "Происшествия"


def test_the_article_page_names_the_defendant() -> None:
    article = KommersantArticleParser().parse(
        RawDocument(
            external_id="8940075",
            url="https://www.kommersant.ru/doc/8940075",
            fetched_at=datetime(2026, 9, 17, tzinfo=UTC),
            content_type="text/html",
            content=ARTICLE_HTML,
        )
    )

    assert article.title.startswith("Крымчанин получил 21 год колонии")
    assert article.published_at is not None
    assert article.published_at.date().isoformat() == "2026-09-09"
    assert "Мамута Белялова задержали" in article.text
    assert "<" not in article.text


def test_a_page_without_article_text_is_rejected() -> None:
    with pytest.raises(ParseError):
        KommersantArticleParser().parse(
            RawDocument(
                external_id="1",
                url="https://www.kommersant.ru/doc/1",
                fetched_at=datetime(2026, 9, 17, tzinfo=UTC),
                content_type="text/html",
                content=b"<html><h1>Title</h1></html>",
            )
        )


def test_the_source_is_registered() -> None:
    definition = get_source_definition("kommersant")

    assert definition.base_url == "https://www.kommersant.ru"
    assert isinstance(definition.create_parser(), KommersantArticleParser)

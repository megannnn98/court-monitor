import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from sources.ingestion_errors import ParseError
from sources.models import RawDocument, SourceReference
from sources.source_registry import SOURCES, get_source_definition
from sources.telegram.article_parser import TelegramPostParser
from sources.telegram.channels import load_telegram_channels
from sources.telegram.listing_parser import TelegramListingParser
from sources.telegram.source_adapter import TelegramSourceAdapter

FIXTURES = Path(__file__).parents[1] / "fixtures"
CHANNEL_HTML = (FIXTURES / "telegram_channel.html").read_bytes()
POST_HTML = (FIXTURES / "telegram_post.html").read_bytes()


class FakeDocumentFetcher:
    async def fetch(self, reference: SourceReference) -> RawDocument:
        raise AssertionError("fetch must not be called during discovery")


def _message(post_id: int, published: str, text: str | None = "Текст") -> str:
    body = f'<div class="tgme_widget_message_text">{text}</div>' if text is not None else ""
    return (
        f'<div class="tgme_widget_message" data-post="chan/{post_id}">{body}'
        f'<a class="tgme_widget_message_date"><time datetime="{published}"></time></a></div>'
    )


def test_listing_parser_returns_channel_posts_newest_first() -> None:
    posts = TelegramListingParser("sudpress53").parse(CHANNEL_HTML)

    assert len(posts) == 13
    assert [post.post_id for post in posts] == sorted(
        (post.post_id for post in posts), reverse=True
    )
    newest = posts[0]
    assert newest.post_id == 6529
    assert newest.published_at == datetime(2026, 9, 7, 13, 20, 9, tzinfo=UTC)
    assert newest.reference == SourceReference(
        external_id="6529", url="https://t.me/sudpress53/6529?embed=1&mode=tme"
    )


def test_listing_parser_skips_posts_of_other_channels() -> None:
    html = _message(1, "2026-09-01T00:00:00+00:00").replace("chan/1", "other/1").encode()

    assert TelegramListingParser("chan").parse(html) == []


def test_post_parser_reads_text_date_and_title() -> None:
    raw = RawDocument(
        external_id="6529",
        url="https://t.me/sudpress53/6529?embed=1&mode=tme",
        fetched_at=datetime(2026, 9, 15, tzinfo=UTC),
        content_type="text/html",
        content=POST_HTML,
    )

    article = TelegramPostParser().parse(raw)

    assert article.text.startswith("ВС РФ рассмотрел спор о размере компенсации за ДТП")
    assert article.published_at == datetime(2026, 9, 7, 13, 20, 9, tzinfo=UTC)
    assert article.title == article.text.split("\n", 1)[0][:120].rstrip() + (
        "…" if len(article.text.split("\n", 1)[0]) > 120 else ""
    )
    assert "<br" not in article.text


def test_post_parser_rejects_a_post_without_text() -> None:
    raw = RawDocument(
        external_id="1",
        url="https://t.me/chan/1?embed=1",
        fetched_at=datetime(2026, 9, 15, tzinfo=UTC),
        content_type="text/html",
        content=_message(1, "2026-09-01T00:00:00+00:00", text=None).encode(),
    )

    with pytest.raises(ParseError):
        TelegramPostParser().parse(raw)


def _discover(pages: dict[str, str], *, limit: int) -> tuple[list[str], list[str]]:
    requested: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=pages[str(request.url)].encode())

    async def run() -> list[SourceReference]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = TelegramSourceAdapter(
                client=client,
                username="chan",
                document_fetcher=FakeDocumentFetcher(),
                page_interval_seconds=0,
                now=lambda: datetime(2026, 9, 15, tzinfo=UTC),
            )
            return await adapter.discover(limit=limit)

    return [reference.external_id for reference in asyncio.run(run())], requested


PAGES = {
    "https://t.me/s/chan": _message(9, "2026-09-14T00:00:00+00:00")
    + _message(10, "2026-09-14T01:00:00+00:00", text=None)
    + _message(11, "2026-09-15T00:00:00+00:00"),
    "https://t.me/s/chan?before=9": _message(7, "2026-08-20T00:00:00+00:00")
    + _message(8, "2026-08-25T00:00:00+00:00"),
    "https://t.me/s/chan?before=7": _message(5, "2026-08-10T00:00:00+00:00")
    + _message(6, "2026-08-17T00:00:00+00:00"),
}


def test_discovery_pages_back_newest_first_skipping_media_and_stops_at_the_history_window() -> None:
    external_ids, requested = _discover(PAGES, limit=100)

    # 30 days before 2026-09-15: post 6 (2026-08-17) is still in, post 5 (2026-08-10) is not.
    assert external_ids == ["11", "9", "8", "7", "6"]
    assert requested == list(PAGES)


def test_discovery_stops_at_the_limit_without_fetching_older_pages() -> None:
    external_ids, requested = _discover(PAGES, limit=2)

    assert external_ids == ["11", "9"]
    assert requested == ["https://t.me/s/chan"]


def test_discovery_stops_when_a_page_has_no_older_posts() -> None:
    pages = {
        "https://t.me/s/chan": _message(2, "2026-09-14T00:00:00+00:00"),
        "https://t.me/s/chan?before=2": _message(2, "2026-09-14T00:00:00+00:00"),
    }

    external_ids, _ = _discover(pages, limit=100)

    assert external_ids == ["2"]


def test_every_channel_of_the_list_is_a_registered_source() -> None:
    channels = load_telegram_channels()

    assert len(channels) == 69
    assert len({channel.source_name for channel in channels}) == 69
    for channel in channels:
        definition = get_source_definition(channel.source_name)
        assert definition.base_url == f"https://t.me/{channel.username}"
        assert definition.source_name == channel.title
    assert {"ovd-info", "sota-vision"} <= set(SOURCES)
    assert get_source_definition("tg-ovdinfolive").source_name == "ОВД-Инфо LIVE"


def test_a_chat_without_web_preview_yields_no_posts_instead_of_failing() -> None:
    """Real case: @pochtasizo212 is a chat; t.me/s/<username> redirects to t.me/<username>."""
    requested: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://t.me/chan"})

    async def run() -> list[SourceReference]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = TelegramSourceAdapter(
                client=client, username="chan", document_fetcher=FakeDocumentFetcher()
            )
            return await adapter.discover(limit=10)

    assert asyncio.run(run()) == []
    assert requested == ["https://t.me/s/chan"]

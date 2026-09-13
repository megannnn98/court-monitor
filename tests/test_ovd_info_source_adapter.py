import asyncio

import httpx
import pytest

from ingestion_errors import PermanentDiscoveryError
from models import RawDocument, SourceReference
from ovd_info_listing_parser import OvdInfoListingParser
from ovd_info_source_adapter import OvdInfoSourceAdapter

LISTING_HTML = b"""
<html>
    <body>
        <a href="/express-news/2026/09/11/article-a">A</a>
        <a href="/express-news/2026/09/10/article-b">B</a>
        <a href="/express-news/2026/09/09/article-c">C</a>
    </body>
</html>
"""


class FakeDocumentFetcher:
    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument:
        raise AssertionError("fetch must not be called during discovery")


def handle_request(request: httpx.Request) -> httpx.Response:
    assert str(request.url) == "https://ovd.info/express-news"

    return httpx.Response(
        200,
        content=LISTING_HTML,
        headers={"content-type": "text/html"},
    )


def test_discover_returns_references_up_to_limit() -> None:
    async def run() -> list[SourceReference]:
        transport = httpx.MockTransport(handle_request)

        async with httpx.AsyncClient(transport=transport) as client:
            adapter = OvdInfoSourceAdapter(
                client=client,
                listing_parser=OvdInfoListingParser(),
                document_fetcher=FakeDocumentFetcher(),
            )

            return await adapter.discover(limit=2)

    references = asyncio.run(run())

    assert [reference.external_id for reference in references] == [
        "/express-news/2026/09/11/article-a",
        "/express-news/2026/09/10/article-b",
    ]


@pytest.mark.parametrize("limit", [0, -1])
def test_discover_rejects_invalid_limit(limit: int) -> None:
    async def run() -> None:
        transport = httpx.MockTransport(handle_request)

        async with httpx.AsyncClient(transport=transport) as client:
            adapter = OvdInfoSourceAdapter(
                client=client,
                listing_parser=OvdInfoListingParser(),
                document_fetcher=FakeDocumentFetcher(),
            )

            await adapter.discover(limit=limit)

    with pytest.raises(
        ValueError,
        match="limit must be greater than zero",
    ):
        asyncio.run(run())


PAGE_0_HTML = b"""
<a href="/express-news/2026/09/11/article-a">A</a>
<a href="/express-news/2026/09/10/article-b">B</a>
"""

PAGE_1_HTML = b"""
<a href="/express-news/2026/09/10/article-b">B duplicate</a>
<a href="/express-news/2026/09/09/article-c">C</a>
<a href="/express-news/2026/09/08/article-d">D</a>
"""


def test_discover_reads_multiple_pages_until_limit() -> None:
    requested_urls: list[str] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested_urls.append(url)

        if url == "https://ovd.info/express-news":
            return httpx.Response(200, content=PAGE_0_HTML)

        if url == "https://ovd.info/express-news?page=1":
            return httpx.Response(200, content=PAGE_1_HTML)

        raise AssertionError(f"unexpected request: {url}")

    async def run() -> list[SourceReference]:
        transport = httpx.MockTransport(handle_request)

        async with httpx.AsyncClient(transport=transport) as client:
            adapter = OvdInfoSourceAdapter(
                client=client,
                listing_parser=OvdInfoListingParser(),
                document_fetcher=FakeDocumentFetcher(),
            )

            return await adapter.discover(limit=3)

    references = asyncio.run(run())

    assert [reference.external_id for reference in references] == [
        "/express-news/2026/09/11/article-a",
        "/express-news/2026/09/10/article-b",
        "/express-news/2026/09/09/article-c",
    ]

    assert requested_urls == [
        "https://ovd.info/express-news",
        "https://ovd.info/express-news?page=1",
    ]


def test_discover_retries_transient_http_error() -> None:
    requests = 0

    def handle_request(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1

        if requests < 3:
            return httpx.Response(
                500,
                request=request,
            )

        return httpx.Response(
            200,
            content=LISTING_HTML,
            request=request,
        )

    async def run() -> list[SourceReference]:
        transport = httpx.MockTransport(handle_request)

        async with httpx.AsyncClient(transport=transport) as client:
            adapter = OvdInfoSourceAdapter(
                client=client,
                listing_parser=OvdInfoListingParser(),
                document_fetcher=FakeDocumentFetcher(),
                max_attempts=3,
                base_delay_seconds=0,
            )

            return await adapter.discover(limit=1)

    references = asyncio.run(run())

    assert len(references) == 1
    assert requests == 3


def test_discover_does_not_retry_permanent_http_error() -> None:
    requests = 0

    def handle_request(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1

        return httpx.Response(
            404,
            request=request,
        )

    async def run() -> None:
        transport = httpx.MockTransport(handle_request)

        async with httpx.AsyncClient(transport=transport) as client:
            adapter = OvdInfoSourceAdapter(
                client=client,
                listing_parser=OvdInfoListingParser(),
                document_fetcher=FakeDocumentFetcher(),
                max_attempts=3,
                base_delay_seconds=0,
            )

            await adapter.discover(limit=1)

    with pytest.raises(PermanentDiscoveryError):
        asyncio.run(run())

    assert requests == 1

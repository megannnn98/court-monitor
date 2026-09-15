import asyncio

import httpx
import pytest

from sources.ingestion_errors import PermanentDiscoveryError
from sources.models import RawDocument, SourceReference
from sources.sota_vision.listing_parser import SotaVisionListingParser
from sources.sota_vision.source_adapter import SotaVisionSourceAdapter

LISTING_HTML = b"""
<html>
    <body>
        <h2 class="entry-title"><a href="/article-a/">A</a></h2>
        <h2 class="entry-title"><a href="/article-b/">B</a></h2>
        <h2 class="entry-title"><a href="/article-c/">C</a></h2>
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
    assert str(request.url) == "https://sota.vision/category/news/"

    return httpx.Response(
        200,
        content=LISTING_HTML,
        headers={"content-type": "text/html"},
    )


def test_discover_returns_references_up_to_limit() -> None:
    async def run() -> list[SourceReference]:
        transport = httpx.MockTransport(handle_request)

        async with httpx.AsyncClient(transport=transport) as client:
            adapter = SotaVisionSourceAdapter(
                client=client,
                listing_parser=SotaVisionListingParser(),
                document_fetcher=FakeDocumentFetcher(),
            )

            return await adapter.discover(limit=2)

    references = asyncio.run(run())

    assert [reference.external_id for reference in references] == [
        "/article-a/",
        "/article-b/",
    ]


@pytest.mark.parametrize("limit", [0, -1])
def test_discover_rejects_invalid_limit(limit: int) -> None:
    async def run() -> None:
        transport = httpx.MockTransport(handle_request)

        async with httpx.AsyncClient(transport=transport) as client:
            adapter = SotaVisionSourceAdapter(
                client=client,
                listing_parser=SotaVisionListingParser(),
                document_fetcher=FakeDocumentFetcher(),
            )

            await adapter.discover(limit=limit)

    with pytest.raises(
        ValueError,
        match="limit must be greater than zero",
    ):
        asyncio.run(run())


PAGE_0_HTML = b"""
<h2 class="entry-title"><a href="/article-a/">A</a></h2>
<h2 class="entry-title"><a href="/article-b/">B</a></h2>
"""

PAGE_1_HTML = b"""
<h2 class="entry-title"><a href="/article-b/">B duplicate</a></h2>
<h2 class="entry-title"><a href="/article-c/">C</a></h2>
<h2 class="entry-title"><a href="/article-d/">D</a></h2>
"""


def test_discover_reads_multiple_pages_until_limit() -> None:
    requested_urls: list[str] = []

    def handle_paginated_request(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested_urls.append(url)

        if url == "https://sota.vision/category/news/":
            return httpx.Response(200, content=PAGE_0_HTML)

        if url == "https://sota.vision/category/news/page/2/":
            return httpx.Response(200, content=PAGE_1_HTML)

        raise AssertionError(f"unexpected request: {url}")

    async def run() -> list[SourceReference]:
        transport = httpx.MockTransport(handle_paginated_request)

        async with httpx.AsyncClient(transport=transport) as client:
            adapter = SotaVisionSourceAdapter(
                client=client,
                listing_parser=SotaVisionListingParser(),
                document_fetcher=FakeDocumentFetcher(),
            )

            return await adapter.discover(limit=3)

    references = asyncio.run(run())

    assert [reference.external_id for reference in references] == [
        "/article-a/",
        "/article-b/",
        "/article-c/",
    ]

    assert requested_urls == [
        "https://sota.vision/category/news/",
        "https://sota.vision/category/news/page/2/",
    ]


def test_discover_retries_transient_http_error() -> None:
    requests = 0

    def handle_flaky_request(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1

        if requests < 3:
            return httpx.Response(500, request=request)

        return httpx.Response(200, content=LISTING_HTML, request=request)

    async def run() -> list[SourceReference]:
        transport = httpx.MockTransport(handle_flaky_request)

        async with httpx.AsyncClient(transport=transport) as client:
            adapter = SotaVisionSourceAdapter(
                client=client,
                listing_parser=SotaVisionListingParser(),
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

    def handle_broken_request(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1

        return httpx.Response(404, request=request)

    async def run() -> None:
        transport = httpx.MockTransport(handle_broken_request)

        async with httpx.AsyncClient(transport=transport) as client:
            adapter = SotaVisionSourceAdapter(
                client=client,
                listing_parser=SotaVisionListingParser(),
                document_fetcher=FakeDocumentFetcher(),
                max_attempts=3,
                base_delay_seconds=0,
            )

            await adapter.discover(limit=1)

    with pytest.raises(PermanentDiscoveryError):
        asyncio.run(run())

    assert requests == 1


def test_discover_stops_at_missing_page_past_the_last_listing_page() -> None:
    """Real case: sota.vision/category/news/page/20/ answers 404 past the archive end.

    A backfill limit larger than the archive must return what was found, not fail.
    """
    requested: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url) == "https://sota.vision/category/news/":
            return httpx.Response(200, content=LISTING_HTML)
        return httpx.Response(404, request=request)

    async def run() -> list[SourceReference]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = SotaVisionSourceAdapter(
                client=client,
                listing_parser=SotaVisionListingParser(),
                document_fetcher=FakeDocumentFetcher(),
                max_attempts=3,
                base_delay_seconds=0,
            )
            return await adapter.discover(limit=100)

    references = asyncio.run(run())

    assert [reference.external_id for reference in references] == [
        "/article-a/",
        "/article-b/",
        "/article-c/",
    ]
    assert requested == [
        "https://sota.vision/category/news/",
        "https://sota.vision/category/news/page/2/",
    ]

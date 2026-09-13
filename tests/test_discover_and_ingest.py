import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from ingestion_errors import ParseError
from main import discover_and_ingest
from models import (
    IngestionResult,
    ParsedArticle,
    PersistenceResult,
    RawDocument,
    SourceReference,
)
from source_registry import SOTA_VISION

LISTING_HTML = b"""
<html>
    <body>
        <a href="/express-news/2026/09/13/article-a">A</a>
        <a href="/express-news/2026/09/12/article-b">B</a>
    </body>
</html>
"""

FAILING_LISTING_HTML = b"""
<html>
    <body>
        <a href="/express-news/2026/09/13/article-a">A</a>
        <a href="/express-news/2026/09/12/article-b">B</a>
        <a href="/express-news/2026/09/11/article-c">C</a>
    </body>
</html>
"""


class FailingPipeline:
    def __init__(self) -> None:
        self.references: list[SourceReference] = []

    async def run(
        self,
        reference: SourceReference,
    ) -> IngestionResult:
        self.references.append(reference)

        if reference.external_id.endswith("article-b"):
            raise ParseError("broken article")

        return IngestionResult(
            article=ParsedArticle(
                external_id=reference.external_id,
                url=reference.url,
                title=reference.external_id,
                published_at=None,
                text="article text",
            ),
            persistence=PersistenceResult(
                document_id=len(self.references),
                article_id=len(self.references),
            ),
        )


class FakeFetcher:
    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument:
        return RawDocument(
            external_id=reference.external_id,
            url=reference.url,
            fetched_at=datetime(2026, 9, 13, tzinfo=UTC),
            content_type="text/html",
            content=b"<html></html>",
        )


class FakePipeline:
    def __init__(self) -> None:
        self.references: list[SourceReference] = []

    async def run(
        self,
        reference: SourceReference,
    ) -> IngestionResult:
        self.references.append(reference)

        return IngestionResult(
            article=ParsedArticle(
                external_id=reference.external_id,
                url=reference.url,
                title=reference.external_id,
                published_at=None,
                text="article text",
            ),
            persistence=PersistenceResult(
                document_id=len(self.references),
                article_id=len(self.references),
            ),
        )


def test_discover_and_ingest_processes_discovered_articles(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://ovd.info/express-news"

        return httpx.Response(
            200,
            content=LISTING_HTML,
            request=request,
        )

    transport = httpx.MockTransport(handle_request)
    original_async_client = httpx.AsyncClient

    def create_client(
        *args: Any,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(
        "main.httpx.AsyncClient",
        create_client,
    )

    pipeline = FakePipeline()
    fetcher = FakeFetcher()

    asyncio.run(
        discover_and_ingest(
            limit=2,
            pipeline=pipeline,
            fetcher=fetcher,
        )
    )

    assert [reference.external_id for reference in pipeline.references] == [
        "/express-news/2026/09/13/article-a",
        "/express-news/2026/09/12/article-b",
    ]

    output = capsys.readouterr().out

    assert "2 saved" in output
    assert "0 failed" in output


def test_discover_and_ingest_continues_after_article_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=FAILING_LISTING_HTML,
            request=request,
        )

    transport = httpx.MockTransport(handle_request)
    original_async_client = httpx.AsyncClient

    def create_client(
        *args: Any,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(
        "main.httpx.AsyncClient",
        create_client,
    )

    pipeline = FailingPipeline()
    fetcher = FakeFetcher()

    asyncio.run(
        discover_and_ingest(
            limit=3,
            pipeline=pipeline,
            fetcher=fetcher,
        )
    )

    assert [reference.external_id for reference in pipeline.references] == [
        "/express-news/2026/09/13/article-a",
        "/express-news/2026/09/12/article-b",
        "/express-news/2026/09/11/article-c",
    ]

    output = capsys.readouterr().out

    assert "article-b" in output
    assert "broken article" in output
    assert "2 saved" in output
    assert "1 failed" in output


def test_discover_and_ingest_handles_empty_discovery(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://ovd.info/express-news"

        return httpx.Response(
            200,
            content=b"<html><body></body></html>",
            request=request,
        )

    transport = httpx.MockTransport(handle_request)
    original_async_client = httpx.AsyncClient

    def create_client(
        *args: Any,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(
        "main.httpx.AsyncClient",
        create_client,
    )

    class MustNotRunPipeline:
        async def run(
            self,
            reference: SourceReference,
        ) -> IngestionResult:
            raise AssertionError(f"pipeline must not run: {reference.url}")

    asyncio.run(
        discover_and_ingest(
            limit=10,
            pipeline=MustNotRunPipeline(),
            fetcher=FakeFetcher(),
        )
    )

    output = capsys.readouterr().out

    assert "completed: 0 saved, 0 failed" in output


def test_discover_and_ingest_respects_limit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    listing_html = b"""
    <html>
        <body>
            <a href="/express-news/2026/09/13/article-a">A</a>
            <a href="/express-news/2026/09/12/article-b">B</a>
            <a href="/express-news/2026/09/11/article-c">C</a>
        </body>
    </html>
    """

    def handle_request(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://ovd.info/express-news"

        return httpx.Response(
            200,
            content=listing_html,
            request=request,
        )

    transport = httpx.MockTransport(handle_request)
    original_async_client = httpx.AsyncClient

    def create_client(
        *args: Any,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(
        "main.httpx.AsyncClient",
        create_client,
    )

    pipeline = FakePipeline()
    fetcher = FakeFetcher()

    asyncio.run(
        discover_and_ingest(
            limit=2,
            pipeline=pipeline,
            fetcher=fetcher,
        )
    )

    assert [reference.external_id for reference in pipeline.references] == [
        "/express-news/2026/09/13/article-a",
        "/express-news/2026/09/12/article-b",
    ]

    output = capsys.readouterr().out

    assert "completed: 2 saved, 0 failed" in output


def test_discover_and_ingest_selects_source_by_definition(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sota_listing_html = b"""
    <html>
        <body>
            <h2 class="entry-title"><a href="/article-a/">A</a></h2>
            <h2 class="entry-title"><a href="/article-b/">B</a></h2>
        </body>
    </html>
    """

    def handle_request(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://sota.vision/category/news/"

        return httpx.Response(
            200,
            content=sota_listing_html,
            request=request,
        )

    transport = httpx.MockTransport(handle_request)
    original_async_client = httpx.AsyncClient

    def create_client(
        *args: Any,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(
        "main.httpx.AsyncClient",
        create_client,
    )

    pipeline = FakePipeline()
    fetcher = FakeFetcher()

    asyncio.run(
        discover_and_ingest(
            limit=2,
            pipeline=pipeline,
            fetcher=fetcher,
            source=SOTA_VISION,
        )
    )

    assert [reference.external_id for reference in pipeline.references] == [
        "/article-a/",
        "/article-b/",
    ]

    output = capsys.readouterr().out

    assert "completed: 2 saved, 0 failed" in output

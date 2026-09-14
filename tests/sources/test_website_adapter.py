import asyncio
from typing import Any

import httpx
import pytest

from sources.ingestion_errors import PermanentFetchError, TransientFetchError
from sources.models import SourceReference
from sources.website_adapter import WebsiteAdapter

REFERENCE = SourceReference(
    external_id="/express-news/test-article",
    url="https://ovd.info/express-news/test-article",
)


def _patch_async_client(
    monkeypatch: pytest.MonkeyPatch,
    transport: httpx.MockTransport,
) -> None:
    original_async_client = httpx.AsyncClient

    def create_client(
        *args: Any,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(
        "sources.website_adapter.httpx.AsyncClient",
        create_client,
    )


def test_fetch_returns_raw_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == REFERENCE.url
        assert request.headers["user-agent"] == "my-app/1.0"

        return httpx.Response(
            status_code=200,
            headers={
                "content-type": "text/html; charset=UTF-8",
            },
            content=b"<html><body>article</body></html>",
            request=request,
        )

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    async def run() -> None:
        adapter = WebsiteAdapter()

        result = await adapter.fetch(REFERENCE)

        assert result.external_id == REFERENCE.external_id
        assert result.url == REFERENCE.url
        assert result.content_type == "text/html; charset=UTF-8"
        assert result.content == b"<html><body>article</body></html>"
        assert result.fetched_at.tzinfo is not None

    asyncio.run(run())


def test_fetch_raises_fetch_error_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=500,
            request=request,
        )

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    async def run() -> None:
        adapter = WebsiteAdapter()

        with pytest.raises(
            TransientFetchError,
            match="Temporary failure fetching",
        ) as exc_info:
            await adapter.fetch(REFERENCE)

        assert isinstance(
            exc_info.value.__cause__,
            httpx.HTTPStatusError,
        )

    asyncio.run(run())


def test_fetch_raises_fetch_error_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "network unavailable",
            request=request,
        )

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    async def run() -> None:
        adapter = WebsiteAdapter()

        with pytest.raises(
            TransientFetchError,
            match="Temporary failure fetching",
        ) as exc_info:
            await adapter.fetch(REFERENCE)

        assert isinstance(
            exc_info.value.__cause__,
            httpx.ConnectError,
        )

    asyncio.run(run())


def test_fetch_raises_permanent_fetch_error_on_http_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=404,
            request=request,
        )

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    async def run() -> None:
        adapter = WebsiteAdapter()

        with pytest.raises(
            PermanentFetchError,
            match="Failed to fetch",
        ) as exc_info:
            await adapter.fetch(REFERENCE)

        assert isinstance(
            exc_info.value.__cause__,
            httpx.HTTPStatusError,
        )

    asyncio.run(run())

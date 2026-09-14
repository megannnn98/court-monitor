"""Monitoring with the real OVD-Info adapter and parsers against a fake HTTP server."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session, sessionmaker

from application import build_monitoring_service
from monitoring.models import MonitoringRunStatus, MonitoringSettings
from sources.models import RawDocument, SourceReference
from sources.source_registry import OVD_INFO

LISTING = b"""
<html><body>
  <a href="/express-news/2026/09/13/sidorov">A</a>
  <a href="/express-news/2026/09/12/petrov">B</a>
</body></html>
"""
ARTICLES = {
    "/express-news/2026/09/13/sidorov": (
        "Задержание правозащитника",
        (
            "Сергей Сидоров, известный правозащитник, задержан на антивоенном митинге. "
            "Активисты считают дело политически мотивированным."
        ),
    ),
    "/express-news/2026/09/12/petrov": (
        "Хулиганство",
        "Полиция задержала Петра Петрова за мелкое хулиганство. Составлен протокол по КоАП.",
    ),
}


def _handler(requests: list[str]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/express-news":
            page = request.url.params.get("page")
            return httpx.Response(200, content=LISTING if page is None else b"<html></html>")
        article = ARTICLES.get(request.url.path)
        if article is None:
            return httpx.Response(404)
        title, paragraph = article
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=(
                f'<html><body><h1 class="express-text-heading">{title}</h1>'
                f'<div class="field--name-field-express-text"><p>{paragraph}</p></div>'
                "</body></html>"
            ).encode(),
        )

    return httpx.MockTransport(handle)


class MockServerFetcher:
    def __init__(self, transport: httpx.MockTransport) -> None:
        self._transport = transport

    async def fetch(self, reference: SourceReference) -> RawDocument:
        async with httpx.AsyncClient(transport=self._transport) as client:
            response = await client.get(reference.url)
            response.raise_for_status()
        return RawDocument(
            external_id=reference.external_id,
            url=str(response.url),
            fetched_at=datetime.now(UTC),
            content_type=response.headers.get("content-type", ""),
            content=response.content,
        )


def test_ovd_info_listing_to_persons_without_internet(
    session_factory: sessionmaker[Session],
) -> None:
    requests: list[str] = []
    transport = _handler(requests)
    service = build_monitoring_service(
        session_factory,
        settings=MonitoringSettings(enabled_sources=("ovd-info",), discovery_limit=5),
        env={},
        sources={OVD_INFO.name: OVD_INFO},
        create_http_client=lambda: httpx.AsyncClient(transport=transport),
        create_fetcher=lambda: MockServerFetcher(transport),
        use_env_semantic_indexer=False,
    )

    first = service.run_source("ovd-info")
    second = service.run_source("ovd-info")

    assert all(url.startswith("https://ovd.info/") for url in requests)
    assert first.status is MonitoringRunStatus.COMPLETED
    assert (first.documents_discovered, first.documents_ingested) == (2, 2)
    assert first.persons_created == 2
    assert first.stage_metrics["classification"]["statuses"] == {
        "political": 1,
        "non_political": 1,
    }
    assert (second.documents_skipped, second.documents_ingested) == (2, 0)
    article_requests = [url for url in requests if "/express-news/2026/" in url]
    assert len(article_requests) == 2

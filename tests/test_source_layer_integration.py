"""End-to-end source-layer tests: listing -> discover -> fetch -> parse -> persist.

No live network is used anywhere in this module: discovery HTTP calls go through
httpx.MockTransport, and article fetches go through a fake DocumentFetcher.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from article_parser import ArticleParser, OvdInfoArticleParser
from ingestion_errors import (
    ParseError,
    PermanentDiscoveryError,
)
from ingestion_pipeline import IngestionPipeline
from models import IngestionResult, ParsedArticle, PersistenceResult, RawDocument, SourceReference
from ovd_info_listing_parser import OvdInfoListingParser
from ovd_info_source_adapter import OvdInfoSourceAdapter
from sota_vision_article_parser import SotaVisionArticleParser
from sota_vision_listing_parser import SotaVisionListingParser
from sota_vision_source_adapter import SotaVisionSourceAdapter
from source_adapter import SourceAdapter
from source_ingestion import SourceIngestion, SourceIngestionFailure


class FakePersistence:
    def __init__(self) -> None:
        self.saved: list[tuple[RawDocument, ParsedArticle]] = []

    def save(
        self,
        raw_document: RawDocument,
        article: ParsedArticle,
    ) -> PersistenceResult:
        self.saved.append((raw_document, article))
        return PersistenceResult(document_id=len(self.saved))


class DictionaryDocumentFetcher:
    """Fake DocumentFetcher returning a pre-scripted RawDocument or exception per external_id."""

    def __init__(
        self,
        outcomes: dict[str, RawDocument | Exception],
    ) -> None:
        self._outcomes = outcomes
        self.calls: list[str] = []

    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument:
        self.calls.append(reference.external_id)
        outcome = self._outcomes[reference.external_id]

        if isinstance(outcome, Exception):
            raise outcome

        return outcome


def _ovd_article_html(title: str, paragraph: str) -> bytes:
    return f"""
    <html>
        <body>
            <h1 class="express-text-heading">{title}</h1>
            <div class="field--name-field-express-text">
                <p>{paragraph}</p>
            </div>
        </body>
    </html>
    """.encode()


def _ovd_broken_article_html() -> bytes:
    return b"<html><body><p>no title here</p></body></html>"


def _sota_article_html(title: str, paragraph: str) -> bytes:
    return f"""
    <html>
        <body>
            <h1 class="entry-title">{title}</h1>
            <div class="entry-content">
                <p>{paragraph}</p>
            </div>
        </body>
    </html>
    """.encode()


def _sota_broken_article_html() -> bytes:
    return b"<html><body><p>no title here</p></body></html>"


class SourceHarness:
    def __init__(
        self,
        *,
        name: str,
        listing_url: str,
        listing_html_for: Callable[[list[str]], bytes],
        build_adapter: Callable[[httpx.AsyncClient, DictionaryDocumentFetcher], SourceAdapter],
        parser: ArticleParser,
        valid_article_html: Callable[[str, str], bytes],
        broken_article_html: Callable[[], bytes],
    ) -> None:
        self.name = name
        self.listing_url = listing_url
        self.listing_html_for = listing_html_for
        self.build_adapter = build_adapter
        self.parser = parser
        self.valid_article_html = valid_article_html
        self.broken_article_html = broken_article_html


def _ovd_harness() -> SourceHarness:
    def listing_html_for(slugs: list[str]) -> bytes:
        links = "".join(f'<a href="/express-news/{slug}">{slug}</a>' for slug in slugs)
        return f"<html><body>{links}</body></html>".encode()

    def build_adapter(
        client: httpx.AsyncClient,
        fetcher: DictionaryDocumentFetcher,
    ) -> SourceAdapter:
        return OvdInfoSourceAdapter(
            client=client,
            listing_parser=OvdInfoListingParser(),
            document_fetcher=fetcher,
            max_attempts=3,
            base_delay_seconds=0,
        )

    return SourceHarness(
        name="ovd-info",
        listing_url="https://ovd.info/express-news",
        listing_html_for=listing_html_for,
        build_adapter=build_adapter,
        parser=OvdInfoArticleParser(),
        valid_article_html=_ovd_article_html,
        broken_article_html=_ovd_broken_article_html,
    )


def _sota_harness() -> SourceHarness:
    def listing_html_for(slugs: list[str]) -> bytes:
        links = "".join(
            f'<h2 class="entry-title"><a href="/{slug}/">{slug}</a></h2>' for slug in slugs
        )
        return f"<html><body>{links}</body></html>".encode()

    def build_adapter(
        client: httpx.AsyncClient,
        fetcher: DictionaryDocumentFetcher,
    ) -> SourceAdapter:
        return SotaVisionSourceAdapter(
            client=client,
            listing_parser=SotaVisionListingParser(),
            document_fetcher=fetcher,
            max_attempts=3,
            base_delay_seconds=0,
        )

    return SourceHarness(
        name="sota-vision",
        listing_url="https://sota.vision/category/news/",
        listing_html_for=listing_html_for,
        build_adapter=build_adapter,
        parser=SotaVisionArticleParser(),
        valid_article_html=_sota_article_html,
        broken_article_html=_sota_broken_article_html,
    )


HARNESSES = [_ovd_harness(), _sota_harness()]


def _external_id(harness: SourceHarness, slug: str) -> str:
    if harness.name == "ovd-info":
        return f"/express-news/{slug}"

    return f"/{slug}/"


async def _run_chain(
    harness: SourceHarness,
    *,
    handle_listing_request: Callable[[httpx.Request], httpx.Response],
    outcomes: dict[str, RawDocument | Exception],
    limit: int,
) -> tuple[list[IngestionResult], list[SourceIngestionFailure], DictionaryDocumentFetcher]:
    transport = httpx.MockTransport(handle_listing_request)
    fetcher = DictionaryDocumentFetcher(outcomes)
    persistence = FakePersistence()

    async with httpx.AsyncClient(transport=transport) as client:
        source_adapter = harness.build_adapter(client, fetcher)
        pipeline = IngestionPipeline(
            source_adapter=fetcher,
            parser=harness.parser,
            persistence=persistence,
        )
        ingestion = SourceIngestion(source_adapter=source_adapter, pipeline=pipeline)

        result = await ingestion.run(limit=limit)

    return result.results, result.failures, fetcher


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_full_chain_ingests_multiple_articles(harness: SourceHarness) -> None:
    slugs = ["article-a", "article-b"]

    requested_urls: list[str] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=harness.listing_html_for(slugs))

    outcomes: dict[str, RawDocument | Exception] = {
        _external_id(harness, slug): RawDocument(
            external_id=_external_id(harness, slug),
            url=f"https://example.test/{slug}",
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
            content_type="text/html",
            content=harness.valid_article_html(f"Title {slug}", f"Text {slug}"),
        )
        for slug in slugs
    }

    results, failures, _fetcher = asyncio.run(
        _run_chain(
            harness,
            handle_listing_request=handle_request,
            outcomes=outcomes,
            limit=10,
        )
    )

    assert requested_urls[0] == harness.listing_url
    assert len(results) == 2
    assert failures == []
    assert [item.article.title for item in results] == ["Title article-a", "Title article-b"]


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_full_chain_respects_limit(harness: SourceHarness) -> None:
    slugs = ["article-a", "article-b", "article-c"]

    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=harness.listing_html_for(slugs))

    outcomes: dict[str, RawDocument | Exception] = {
        _external_id(harness, slug): RawDocument(
            external_id=_external_id(harness, slug),
            url=f"https://example.test/{slug}",
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
            content_type="text/html",
            content=harness.valid_article_html(f"Title {slug}", f"Text {slug}"),
        )
        for slug in slugs
    }

    results, failures, fetcher = asyncio.run(
        _run_chain(
            harness,
            handle_listing_request=handle_request,
            outcomes=outcomes,
            limit=2,
        )
    )

    assert len(results) == 2
    assert failures == []
    assert fetcher.calls == [_external_id(harness, "article-a"), _external_id(harness, "article-b")]


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_full_chain_deduplicates_repeated_links(harness: SourceHarness) -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=harness.listing_html_for(["article-a", "article-a", "article-b"]),
        )

    outcomes: dict[str, RawDocument | Exception] = {
        _external_id(harness, slug): RawDocument(
            external_id=_external_id(harness, slug),
            url=f"https://example.test/{slug}",
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
            content_type="text/html",
            content=harness.valid_article_html(f"Title {slug}", f"Text {slug}"),
        )
        for slug in ["article-a", "article-b"]
    }

    results, failures, fetcher = asyncio.run(
        _run_chain(
            harness,
            handle_listing_request=handle_request,
            outcomes=outcomes,
            limit=10,
        )
    )

    assert len(results) == 2
    assert failures == []
    assert fetcher.calls == [_external_id(harness, "article-a"), _external_id(harness, "article-b")]


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_full_chain_handles_empty_listing(harness: SourceHarness) -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html><body></body></html>")

    results, failures, fetcher = asyncio.run(
        _run_chain(
            harness,
            handle_listing_request=handle_request,
            outcomes={},
            limit=10,
        )
    )

    assert results == []
    assert failures == []
    assert fetcher.calls == []


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_full_chain_recovers_from_transient_listing_failure(harness: SourceHarness) -> None:
    slugs = ["article-a"]
    attempts = 0

    def handle_request(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1

        if attempts < 2:
            return httpx.Response(500, request=request)

        return httpx.Response(200, content=harness.listing_html_for(slugs), request=request)

    outcomes: dict[str, RawDocument | Exception] = {
        _external_id(harness, "article-a"): RawDocument(
            external_id=_external_id(harness, "article-a"),
            url="https://example.test/article-a",
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
            content_type="text/html",
            content=harness.valid_article_html("Title", "Text"),
        )
    }

    results, failures, _fetcher = asyncio.run(
        _run_chain(
            harness,
            handle_listing_request=handle_request,
            outcomes=outcomes,
            limit=1,
        )
    )

    assert len(results) == 1
    assert failures == []
    assert attempts == 2


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_full_chain_raises_on_permanent_listing_failure(harness: SourceHarness) -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    async def run() -> None:
        await _run_chain(
            harness,
            handle_listing_request=handle_request,
            outcomes={},
            limit=1,
        )

    with pytest.raises(PermanentDiscoveryError):
        asyncio.run(run())


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_full_chain_continues_after_one_article_parse_error(harness: SourceHarness) -> None:
    slugs = ["article-a", "article-b", "article-c"]

    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=harness.listing_html_for(slugs))

    outcomes: dict[str, RawDocument | Exception] = {
        _external_id(harness, "article-a"): RawDocument(
            external_id=_external_id(harness, "article-a"),
            url="https://example.test/article-a",
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
            content_type="text/html",
            content=harness.valid_article_html("Title A", "Text A"),
        ),
        _external_id(harness, "article-b"): RawDocument(
            external_id=_external_id(harness, "article-b"),
            url="https://example.test/article-b",
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
            content_type="text/html",
            content=harness.broken_article_html(),
        ),
        _external_id(harness, "article-c"): RawDocument(
            external_id=_external_id(harness, "article-c"),
            url="https://example.test/article-c",
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
            content_type="text/html",
            content=harness.valid_article_html("Title C", "Text C"),
        ),
    }

    results, failures, _fetcher = asyncio.run(
        _run_chain(
            harness,
            handle_listing_request=handle_request,
            outcomes=outcomes,
            limit=10,
        )
    )

    assert [item.article.title for item in results] == ["Title A", "Title C"]
    assert len(failures) == 1
    assert isinstance(failures[0].error, ParseError)
    assert failures[0].reference.external_id == _external_id(harness, "article-b")

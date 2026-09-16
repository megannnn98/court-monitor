import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from sources.models import RawDocument, SourceReference
from sources.sudrf.listing_parser import SudrfListingParser
from sources.sudrf.source_adapter import SudrfSourceAdapter

HOST = "2zovs.msk.sudrf.ru"
LISTING_URL = "https://2zovs.msk.sudrf.ru/modules.php?name=press_dep"
ARCHIVE_2026_URL = f"{LISTING_URL}&op=12&arc_list=2026"
ARCHIVE_2025_URL = f"{LISTING_URL}&op=12&arc_list=2025"


def _news_list(dids: list[int], *, container: str = "divNewsList") -> bytes:
    rows = "".join(
        f"<a href='/modules.php?name=press_dep&op=1&did={did}'>News {did}</a>" for did in dids
    )
    archive = (
        "<div id='divArchiveSelector'>"
        "<a href='/modules.php?name=press_dep&op=12&arc_list=2026'>2026</a>"
        "<a href='/modules.php?name=press_dep&op=12&arc_list=2025'>2025</a>"
        "</div>"
    )
    return f"{archive}<div id='{container}'>{rows}</div>".encode()


class FakeDocumentFetcher:
    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument:
        raise AssertionError("fetch must not be called during discovery")


def _discover(pages: dict[str, bytes], *, limit: int) -> tuple[list[str], list[str]]:
    requested: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)

        if url not in pages:
            return httpx.Response(404)

        return httpx.Response(200, content=pages[url], headers={"content-type": "text/html"})

    async def run() -> list[SourceReference]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = SudrfSourceAdapter(
                client=client,
                listing_parser=SudrfListingParser(HOST),
                document_fetcher=FakeDocumentFetcher(),
                host=HOST,
            )

            return await adapter.discover(limit=limit)

    references = asyncio.run(run())

    return [reference.external_id for reference in references], requested


def test_discover_returns_the_latest_news_without_touching_the_archive() -> None:
    external_ids, requested = _discover(
        {LISTING_URL: _news_list([397, 396, 395])},
        limit=2,
    )

    assert external_ids == ["397", "396"]
    assert requested == [LISTING_URL]


def test_discover_walks_year_archives_newest_first_once_the_news_page_runs_out() -> None:
    external_ids, requested = _discover(
        {
            LISTING_URL: _news_list([397, 396]),
            ARCHIVE_2026_URL: _news_list([397, 396, 364], container="divArchiveList"),
            ARCHIVE_2025_URL: _news_list([120, 119], container="divArchiveList"),
        },
        limit=5,
    )

    assert external_ids == ["397", "396", "364", "120", "119"]
    assert requested == [LISTING_URL, ARCHIVE_2026_URL, ARCHIVE_2025_URL]


def test_discover_stops_requesting_archives_as_soon_as_the_limit_is_reached() -> None:
    external_ids, requested = _discover(
        {
            LISTING_URL: _news_list([397, 396]),
            ARCHIVE_2026_URL: _news_list([397, 396, 364], container="divArchiveList"),
            ARCHIVE_2025_URL: _news_list([120], container="divArchiveList"),
        },
        limit=3,
    )

    assert external_ids == ["397", "396", "364"]
    assert requested == [LISTING_URL, ARCHIVE_2026_URL]


@pytest.mark.parametrize("limit", [0, -1])
def test_discover_rejects_invalid_limit(limit: int) -> None:
    with pytest.raises(ValueError, match="limit must be greater than zero"):
        _discover({LISTING_URL: _news_list([397])}, limit=limit)


def test_discover_fetches_one_document_through_the_injected_fetcher() -> None:
    reference = SourceReference(external_id="369", url=f"{LISTING_URL}&op=1&did=369")
    document = RawDocument(
        external_id="369",
        url=reference.url,
        fetched_at=datetime(2026, 9, 16, tzinfo=UTC),
        content_type="text/html",
        content=b"page",
    )

    class RecordingFetcher:
        async def fetch(self, ref: SourceReference) -> RawDocument:
            assert ref == reference
            return document

    async def run() -> RawDocument:
        async with httpx.AsyncClient() as client:
            adapter = SudrfSourceAdapter(
                client=client,
                listing_parser=SudrfListingParser(HOST),
                document_fetcher=RecordingFetcher(),
                host=HOST,
            )

            return await adapter.fetch(reference)

    assert asyncio.run(run()) is document

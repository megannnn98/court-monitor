import httpx

from ingestion.sources.discovery_pagination import fetch_listing_page_with_retry
from ingestion.sources.source_adapter import KnownIds
from ingestion.sources.sudrf.listing_parser import SudrfListingParser
from ingestion.sources.sudrf.reference import LISTING_QUERY, court_url
from monitor_core.model import RawDocument, SourceReference
from monitor_core.ports import DocumentFetcher


class SudrfSourceAdapter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        listing_parser: SudrfListingParser,
        document_fetcher: DocumentFetcher,
        *,
        host: str,
        max_attempts: int = 3,
        base_delay_seconds: float = 0.5,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero")

        if base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must not be negative")

        self._client = client
        self._listing_parser = listing_parser
        self._document_fetcher = document_fetcher
        self._host = host
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds

    async def discover(
        self,
        *,
        limit: int,
    ) -> list[SourceReference]:
        """The latest-news page, then each per-year archive, newest year first.

        The site paginates by year rather than by page number: the latest-news page
        shows the most recent 30 items and every older item is listed on the archive
        page of its year, so a year is one request.
        """
        return await self._discover(limit=limit, known=None)

    async def discover_until_known(self, *, limit: int, known: KnownIds) -> list[SourceReference]:
        """`discover`, but no further than a page (the latest news, a year) that is all
        stored: the years before it were loaded by an earlier run."""
        return await self._discover(limit=limit, known=known)

    async def _discover(self, *, limit: int, known: KnownIds | None) -> list[SourceReference]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")

        references: list[SourceReference] = []
        seen_external_ids: set[str] = set()

        content = await self._fetch_listing_page(court_url(self._host, LISTING_QUERY))
        if self._collect(
            self._listing_parser.parse(content), references, seen_external_ids, limit, known
        ):
            return references

        for archive_url in self._listing_parser.parse_archive_years(content):
            if len(references) == limit:
                break

            archive = await self._fetch_listing_page(archive_url)
            if self._collect(
                self._listing_parser.parse(archive), references, seen_external_ids, limit, known
            ):
                break

        return references

    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument:
        return await self._document_fetcher.fetch(reference)

    @staticmethod
    def _collect(
        page_references: list[SourceReference],
        references: list[SourceReference],
        seen_external_ids: set[str],
        limit: int,
        known: KnownIds | None,
    ) -> bool:
        """Add the page's new references; whether paging stops here — the page brought
        something, and all of it is stored already."""
        added: list[str] = []
        for reference in page_references:
            if len(references) == limit:
                break

            if reference.external_id in seen_external_ids:
                continue

            seen_external_ids.add(reference.external_id)
            references.append(reference)
            added.append(reference.external_id)
        return known is not None and bool(added) and set(added) <= known(added)

    async def _fetch_listing_page(
        self,
        url: str,
    ) -> bytes:
        return await fetch_listing_page_with_retry(
            self._client,
            url,
            max_attempts=self._max_attempts,
            base_delay_seconds=self._base_delay_seconds,
        )

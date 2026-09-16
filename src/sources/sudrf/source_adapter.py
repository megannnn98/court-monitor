import httpx

from sources.discovery_pagination import fetch_listing_page_with_retry
from sources.models import RawDocument, SourceReference
from sources.source_adapter import DocumentFetcher
from sources.sudrf.listing_parser import SudrfListingParser
from sources.sudrf.reference import LISTING_QUERY, court_url


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
        if limit < 1:
            raise ValueError("limit must be greater than zero")

        references: list[SourceReference] = []
        seen_external_ids: set[str] = set()

        content = await self._fetch_listing_page(court_url(self._host, LISTING_QUERY))
        self._collect(self._listing_parser.parse(content), references, seen_external_ids, limit)

        for archive_url in self._listing_parser.parse_archive_years(content):
            if len(references) == limit:
                break

            archive = await self._fetch_listing_page(archive_url)
            self._collect(self._listing_parser.parse(archive), references, seen_external_ids, limit)

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
    ) -> None:
        for reference in page_references:
            if len(references) == limit:
                return

            if reference.external_id in seen_external_ids:
                continue

            seen_external_ids.add(reference.external_id)
            references.append(reference)

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

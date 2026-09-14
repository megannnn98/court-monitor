import httpx

from sources.discovery_pagination import (
    discover_paginated_references,
    fetch_listing_page_with_retry,
)
from sources.models import RawDocument, SourceReference
from sources.ovd_info.listing_parser import OvdInfoListingParser
from sources.source_adapter import DocumentFetcher

OVD_INFO_LISTING_URL = "https://ovd.info/express-news"


class OvdInfoSourceAdapter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        listing_parser: OvdInfoListingParser,
        document_fetcher: DocumentFetcher,
        *,
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
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds

    @staticmethod
    def _listing_url(page: int) -> str:
        if page == 0:
            return OVD_INFO_LISTING_URL

        return f"{OVD_INFO_LISTING_URL}?page={page}"

    async def discover(
        self,
        *,
        limit: int,
    ) -> list[SourceReference]:
        return await discover_paginated_references(
            limit=limit,
            listing_url_for_page=self._listing_url,
            fetch_page=self._fetch_listing_page,
            parse_page=self._listing_parser.parse,
        )

    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument:
        return await self._document_fetcher.fetch(reference)

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

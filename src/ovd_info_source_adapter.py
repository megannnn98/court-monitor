import asyncio

import httpx

from ingestion_errors import PermanentDiscoveryError, TransientDiscoveryError
from models import RawDocument, SourceReference
from ovd_info_listing_parser import OvdInfoListingParser
from source_adapter import DocumentFetcher

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
        if limit < 1:
            raise ValueError("limit must be greater than zero")

        references: list[SourceReference] = []
        seen_external_ids: set[str] = set()
        page = 0

        while len(references) < limit:
            content = await self._fetch_listing_page(self._listing_url(page))

            page_references = self._listing_parser.parse(content)

            if not page_references:
                break

            added = 0

            for reference in page_references:
                if reference.external_id in seen_external_ids:
                    continue

                seen_external_ids.add(reference.external_id)
                references.append(reference)
                added += 1

                if len(references) == limit:
                    break

            if added == 0:
                break

            page += 1

        return references

    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument:
        return await self._document_fetcher.fetch(reference)

    async def _fetch_listing_page(
        self,
        url: str,
    ) -> bytes:
        for attempt in range(self._max_attempts):
            try:
                response = await self._client.get(url)
                response.raise_for_status()
                return response.content

            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code

                retryable = status_code == 429 or status_code >= 500

                if not retryable:
                    raise PermanentDiscoveryError(
                        f"Failed to discover articles: HTTP {status_code}"
                    ) from exc

                if attempt == self._max_attempts - 1:
                    raise TransientDiscoveryError(
                        f"Temporary discovery failure: HTTP {status_code}"
                    ) from exc

            except httpx.TransportError as exc:
                if attempt == self._max_attempts - 1:
                    raise TransientDiscoveryError("Temporary discovery failure") from exc

            delay = self._base_delay_seconds * (2**attempt)
            await asyncio.sleep(delay)

        raise AssertionError("unreachable")

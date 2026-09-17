"""The figurant registry of «Поддержка политзаключённых. Мемориал» as a source.

The registry is a WordPress REST collection (`/wp-json/wp/v2/figurant`): one card per
persecuted person, with the full name and the case as taxonomy slugs. A listing page
already carries every field the parser reads, so discovery keeps the cards it lists and
`fetch` serves them from there: one request per hundred people instead of one per person,
which matters under the site's `Crawl-delay: 10`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from sources.discovery_pagination import fetch_listing_page_with_retry
from sources.ingestion_errors import PermanentDiscoveryError
from sources.models import RawDocument, SourceReference
from sources.source_adapter import DocumentFetcher

MEMOPZK_BASE_URL = "https://memopzk.org"
FIGURANT_COLLECTION_URL = f"{MEMOPZK_BASE_URL}/wp-json/wp/v2/figurant"
FIGURANT_CONTENT_TYPE = "application/vnd.memopzk.figurant+json"
# The fields the parser reads; the rest of a card (rendered page, SEO data) is not fetched.
FIGURANT_FIELDS = "id,date_gmt,modified_gmt,link,slug,title,class_list"
PAGE_SIZE = 100
# robots.txt: `Crawl-delay: 10`.
CRAWL_DELAY_SECONDS = 10.0


class MemopzkFigurantAdapter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        document_fetcher: DocumentFetcher,
        *,
        max_attempts: int = 3,
        base_delay_seconds: float = 0.5,
        crawl_delay_seconds: float = CRAWL_DELAY_SECONDS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero")
        self._client = client
        self._document_fetcher = document_fetcher
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._crawl_delay_seconds = crawl_delay_seconds
        self._now = now
        self._cards: dict[str, dict[str, Any]] = {}

    async def discover(self, *, limit: int) -> list[SourceReference]:
        """The most recently changed cards first, up to `limit`."""
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        references: list[SourceReference] = []
        page = 1
        while True:
            try:
                content = await fetch_listing_page_with_retry(
                    self._client,
                    _listing_url(page, min(PAGE_SIZE, limit)),
                    max_attempts=self._max_attempts,
                    base_delay_seconds=self._base_delay_seconds,
                )
            except PermanentDiscoveryError:
                # WordPress answers 400 for a page past the last one.
                if page == 1:
                    raise
                return references
            cards = json.loads(content)
            if not isinstance(cards, list) or not cards:
                return references
            for card in cards:
                reference = _reference(card)
                self._cards[reference.external_id] = card
                references.append(reference)
                if len(references) == limit:
                    return references
            if len(cards) < min(PAGE_SIZE, limit):
                return references
            page += 1
            await asyncio.sleep(self._crawl_delay_seconds)

    async def fetch(self, reference: SourceReference) -> RawDocument:
        card = self._cards.get(reference.external_id)
        if card is None:
            # Not listed by this adapter (research fetches a card by its page URL).
            card = await self._fetch_card(reference.external_id)
        return RawDocument(
            external_id=reference.external_id,
            url=reference.url,
            fetched_at=self._now(),
            content_type=FIGURANT_CONTENT_TYPE,
            content=json.dumps(card, ensure_ascii=False).encode(),
        )

    async def _fetch_card(self, external_id: str) -> dict[str, Any]:
        content = await fetch_listing_page_with_retry(
            self._client,
            f"{FIGURANT_COLLECTION_URL}/{external_id}?_fields={FIGURANT_FIELDS}",
            max_attempts=self._max_attempts,
            base_delay_seconds=self._base_delay_seconds,
        )
        card = json.loads(content)
        if not isinstance(card, dict):
            raise TypeError(f"Unexpected figurant card {external_id}")
        return card


def _listing_url(page: int, per_page: int) -> str:
    return (
        f"{FIGURANT_COLLECTION_URL}?per_page={per_page}&page={page}"
        f"&orderby=modified&order=desc&_fields={FIGURANT_FIELDS}"
    )


def _reference(card: dict[str, Any]) -> SourceReference:
    return SourceReference(external_id=str(card["id"]), url=str(card["link"]))

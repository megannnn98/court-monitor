import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from sources.discovery_pagination import fetch_listing_page_with_retry
from sources.models import RawDocument, SourceReference
from sources.source_adapter import DocumentFetcher
from sources.telegram.listing_parser import TelegramListingParser

# Discovery never goes further back than this: the first run of a channel loads a month.
TELEGRAM_HISTORY_DAYS = 30
# Politeness towards t.me between listing pages of one channel.
LISTING_PAGE_INTERVAL_SECONDS = 1.0


class TelegramSourceAdapter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        username: str,
        document_fetcher: DocumentFetcher,
        *,
        max_attempts: int = 3,
        base_delay_seconds: float = 0.5,
        history_days: int = TELEGRAM_HISTORY_DAYS,
        page_interval_seconds: float = LISTING_PAGE_INTERVAL_SECONDS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero")
        self._client = client
        self._username = username
        self._listing_parser = TelegramListingParser(username)
        self._document_fetcher = document_fetcher
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._history_days = history_days
        self._page_interval_seconds = page_interval_seconds
        self._now = now

    def _listing_url(self, before: int | None) -> str:
        url = f"https://t.me/s/{self._username}"
        return url if before is None else f"{url}?before={before}"

    async def discover(
        self,
        *,
        limit: int,
    ) -> list[SourceReference]:
        """Newest text posts first, up to `limit` and no older than the history window."""
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        cutoff = self._now() - timedelta(days=self._history_days)
        references: list[SourceReference] = []
        before: int | None = None
        while True:
            content = await fetch_listing_page_with_retry(
                self._client,
                self._listing_url(before),
                max_attempts=self._max_attempts,
                base_delay_seconds=self._base_delay_seconds,
            )
            posts = self._listing_parser.parse(content)
            # A page with no post older than the previous one is the end of the channel.
            posts = [post for post in posts if before is None or post.post_id < before]
            if not posts:
                return references
            for post in posts:
                if post.published_at < cutoff:
                    return references
                # Media-only posts have nothing to extract.
                if post.has_text:
                    references.append(post.reference)
                    if len(references) == limit:
                        return references
            before = posts[-1].post_id
            await asyncio.sleep(self._page_interval_seconds)

    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument:
        return await self._document_fetcher.fetch(reference)

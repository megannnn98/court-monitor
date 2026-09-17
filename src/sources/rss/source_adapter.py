"""A news site as a source through its RSS feed, filtered before any page is fetched.

A general newsroom publishes hundreds of items a day and a few of them are about
persecution. The feed carries each item's section, title and lead, so the filter decides
from those, and only the items it keeps cost a page request.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

from sources.discovery_pagination import fetch_listing_page_with_retry
from sources.ingestion_errors import PermanentDiscoveryError
from sources.models import RawDocument, SourceReference
from sources.source_adapter import DocumentFetcher


@dataclass(frozen=True)
class RssItem:
    link: str
    title: str
    description: str
    category: str
    published_at: datetime | None


class RssSourceAdapter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        feed_url: str,
        document_fetcher: DocumentFetcher,
        *,
        include: Callable[[RssItem], bool],
        external_id: Callable[[str], str],
        max_attempts: int = 3,
        base_delay_seconds: float = 0.5,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero")
        self._client = client
        self._feed_url = feed_url
        self._document_fetcher = document_fetcher
        self._include = include
        self._external_id = external_id
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds

    async def discover(self, *, limit: int) -> list[SourceReference]:
        """The feed's items the filter keeps, newest first, up to `limit`."""
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        content = await fetch_listing_page_with_retry(
            self._client,
            self._feed_url,
            max_attempts=self._max_attempts,
            base_delay_seconds=self._base_delay_seconds,
        )
        items = [item for item in parse_rss(content) if self._include(item)]
        return [
            SourceReference(external_id=self._external_id(item.link), url=item.link)
            for item in items[:limit]
        ]

    async def fetch(self, reference: SourceReference) -> RawDocument:
        return await self._document_fetcher.fetch(reference)


def parse_rss(content: bytes) -> list[RssItem]:
    """The items of an RSS 2.0 feed in feed order (newest first, as feeds publish them).

    The standard library parser is enough here: expat 2.4+ refuses entity amplification.
    """
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise PermanentDiscoveryError("Unreadable RSS feed") from exc
    items: list[RssItem] = []
    for node in root.iter("item"):
        link = _text(node, "link") or _text(node, "guid")
        if not link:
            continue
        items.append(
            RssItem(
                link=link,
                title=_text(node, "title"),
                description=_text(node, "description"),
                category=_text(node, "category"),
                published_at=_date(_text(node, "pubDate")),
            )
        )
    return items


def _text(node: ElementTree.Element, tag: str) -> str:
    child = node.find(tag)
    return " ".join((child.text or "").split()) if child is not None else ""


def _date(value: str) -> datetime | None:
    try:
        return parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError):
        return None

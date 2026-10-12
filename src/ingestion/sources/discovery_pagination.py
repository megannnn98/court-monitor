import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx

from ingestion.sources.source_adapter import KnownIds
from monitor_core.errors import (
    ListingPageNotFoundError,
    PermanentDiscoveryError,
    TransientDiscoveryError,
)
from monitor_core.model import SourceReference

logger = logging.getLogger("sources")


async def fetch_listing_page_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_attempts: int,
    base_delay_seconds: float,
) -> bytes:
    for attempt in range(max_attempts):
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code

            retryable = status_code == 429 or status_code >= 500

            if status_code == 404:
                raise ListingPageNotFoundError(
                    f"Failed to discover articles: HTTP {status_code}"
                ) from exc

            if not retryable:
                raise PermanentDiscoveryError(
                    f"Failed to discover articles: HTTP {status_code}"
                ) from exc

            if attempt == max_attempts - 1:
                raise TransientDiscoveryError(
                    f"Temporary discovery failure: HTTP {status_code}"
                ) from exc

        except httpx.TransportError as exc:
            if attempt == max_attempts - 1:
                raise TransientDiscoveryError("Temporary discovery failure") from exc

        delay = base_delay_seconds * (2**attempt)
        await asyncio.sleep(delay)

    raise AssertionError("unreachable")


async def discover_paginated_references(
    *,
    limit: int,
    listing_url_for_page: Callable[[int], str],
    fetch_page: Callable[[str], Awaitable[bytes]],
    parse_page: Callable[[bytes], list[SourceReference]],
    known: KnownIds | None = None,
) -> list[SourceReference]:
    """The listing's references, newest first, up to `limit`.

    With `known`, paging stops after a page all of whose references are stored: past it
    lie publications an earlier run already loaded. `limit` is then only a ceiling, and
    a run after a long pause reaches back to where the last one stopped."""
    if limit < 1:
        raise ValueError("limit must be greater than zero")

    references: list[SourceReference] = []
    seen_external_ids: set[str] = set()
    page = 0

    while len(references) < limit:
        try:
            content = await fetch_page(listing_url_for_page(page))
        except ListingPageNotFoundError:
            # Past the last page of the archive; a missing first page is still an error.
            if page == 0:
                raise
            logger.warning(
                "event=discovery_stopped_at_missing_page page=%d references=%d",
                page,
                len(references),
            )
            break

        page_references = parse_page(content)

        if not page_references:
            break

        added: list[str] = []

        for reference in page_references:
            if reference.external_id in seen_external_ids:
                continue

            seen_external_ids.add(reference.external_id)
            references.append(reference)
            added.append(reference.external_id)

            if len(references) == limit:
                break

        if not added:
            break
        if known is not None and set(added) <= known(added):
            logger.info(
                "event=discovery_reached_known page=%d references=%d", page, len(references)
            )
            break

        page += 1

    return references

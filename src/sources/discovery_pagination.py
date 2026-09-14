import asyncio
from collections.abc import Awaitable, Callable

import httpx

from sources.ingestion_errors import PermanentDiscoveryError, TransientDiscoveryError
from sources.models import SourceReference


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
) -> list[SourceReference]:
    if limit < 1:
        raise ValueError("limit must be greater than zero")

    references: list[SourceReference] = []
    seen_external_ids: set[str] = set()
    page = 0

    while len(references) < limit:
        content = await fetch_page(listing_url_for_page(page))

        page_references = parse_page(content)

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

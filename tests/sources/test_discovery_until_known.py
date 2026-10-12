"""A regular run pages back until it reaches what is stored: the limit is a ceiling.

With a limit of 50 and no such stop, a source with more publications between two runs
lost the older ones for good — the next run started from the newest again."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from ingestion.sources.discovery_pagination import discover_paginated_references
from ingestion.sources.memopzk.source_adapter import MemopzkFigurantAdapter
from ingestion.sources.ovd_info.source_adapter import OvdInfoSourceAdapter
from ingestion.sources.sota_vision.source_adapter import SotaVisionSourceAdapter
from ingestion.sources.source_adapter import DiscoversUntilKnown
from ingestion.sources.sudrf.source_adapter import SudrfSourceAdapter
from ingestion.sources.telegram.source_adapter import TelegramSourceAdapter
from monitor_core.errors import ListingPageNotFoundError
from monitor_core.model import SourceReference

# Three listing pages of three publications, newest first: 9…1.
PAGES = [["9", "8", "7"], ["6", "5", "4"], ["3", "2", "1"]]


def _paged(known: set[str] | None, *, limit: int = 100) -> tuple[list[str], int]:
    read: list[int] = []

    async def fetch(url: str) -> bytes:
        page = int(url)
        if page >= len(PAGES):
            raise ListingPageNotFoundError("past the last page")
        read.append(page)
        return ",".join(PAGES[page]).encode()

    def stored(ids: Sequence[str]) -> set[str]:
        assert known is not None
        return set(ids) & known

    references = asyncio.run(
        discover_paginated_references(
            limit=limit,
            listing_url_for_page=str,
            fetch_page=fetch,
            parse_page=lambda content: [
                SourceReference(external_id=item, url=f"https://example.test/{item}")
                for item in content.decode().split(",")
            ],
            known=None if known is None else stored,
        )
    )
    return [reference.external_id for reference in references], len(read)


@pytest.mark.parametrize(
    ("known", "found", "pages_read"),
    [
        # Nothing stored: the whole listing.
        (set(), ["9", "8", "7", "6", "5", "4", "3", "2", "1"], 3),
        # The last run stopped at 6: the page of 6, 5, 4 is all stored, paging ends there.
        ({"6", "5", "4", "3", "2", "1"}, ["9", "8", "7", "6", "5", "4"], 2),
        # A page with one new publication among stored ones is not the end.
        ({"9", "8", "6", "5", "4"}, ["9", "8", "7", "6", "5", "4"], 2),
        # Everything is stored: one page.
        ({"9", "8", "7", "6", "5", "4", "3", "2", "1"}, ["9", "8", "7"], 1),
    ],
)
def test_paging_ends_after_a_page_that_is_all_stored(
    known: set[str], found: list[str], pages_read: int
) -> None:
    assert _paged(known) == (found, pages_read)


def test_without_the_stop_the_listing_is_paged_to_the_limit() -> None:
    """A backfill goes deep on purpose: stored pages do not end it."""
    assert _paged(None) == (["9", "8", "7", "6", "5", "4", "3", "2", "1"], 3)
    assert _paged(None, limit=4) == (["9", "8", "7", "6"], 2)


def test_the_limit_is_still_a_ceiling_when_nothing_is_stored() -> None:
    assert _paged(set(), limit=5) == (["9", "8", "7", "6", "5"], 2)


@pytest.mark.parametrize(
    "adapter",
    [
        OvdInfoSourceAdapter,
        SotaVisionSourceAdapter,
        SudrfSourceAdapter,
        MemopzkFigurantAdapter,
        TelegramSourceAdapter,
    ],
)
def test_every_paging_source_can_stop_at_what_is_stored(adapter: type) -> None:
    """The service asks only a source that says it can (`DiscoversUntilKnown`); one that
    cannot is paged to the limit at every run."""
    assert issubclass(adapter, DiscoversUntilKnown)

"""The ingestion module's public API: what a caller may ask of a source.

One unit of work per call, so the caller keeps its own loop: its heartbeat, its record
of a failed document and its counters. Nothing here knows about runs.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from itertools import islice
from typing import Protocol

import httpx

from ingestion.sources.source_adapter import DiscoversUntilKnown
from ingestion.sources.source_registry import SourceDefinition
from ingestion.sources.sqlalchemy_persistence import (
    SqlAlchemyIngestionPersistence,
    SqlAlchemyPersistenceResult,
)
from ingestion.sources.website_adapter import bind_http_client
from monitor_core.errors import NoTextError
from monitor_core.ingestion import FetchedArticle, IngestionPipeline
from monitor_core.model import ParsedArticle, SourceReference
from monitor_core.ports import DocumentFetcher


class StoredDocuments(Protocol):
    """Which documents of a source the database already holds."""

    def known_external_ids(
        self, *, source_base_url: str, external_ids: Sequence[str]
    ) -> set[str]: ...


class SkipReason(StrEnum):
    """Why a document that was read is not stored. None of them is a failure."""

    NO_TEXT = "no_text"
    UNKNOWN_PUBLISHED_DATE = "unknown_published_date"
    OUT_OF_PUBLISHED_RANGE = "out_of_published_range"


@dataclass(frozen=True)
class ReadDocument:
    """A reference fetched and parsed: to be stored, or skipped and why."""

    skipped: SkipReason | None = None
    published_at: datetime | None = None
    # What `store` saves; None for a skipped document.
    fetched: FetchedArticle | None = None


def _published_skip(
    article: ParsedArticle, *, published_from: date | None, published_to: date | None
) -> SkipReason | None:
    if published_from is None and published_to is None:
        return None
    if article.published_at is None:
        return SkipReason.UNKNOWN_PUBLISHED_DATE
    published_on = article.published_at.date()
    if (published_from is None or published_on >= published_from) and (
        published_to is None or published_on <= published_to
    ):
        return None
    return SkipReason.OUT_OF_PUBLISHED_RANGE


class SourceIngestion:
    """The documents of one source over one HTTP client, one reference per call."""

    def __init__(
        self,
        pipeline: IngestionPipeline[SqlAlchemyPersistenceResult],
        *,
        published_from: date | None,
        published_to: date | None,
    ) -> None:
        self._pipeline = pipeline
        self._published_from = published_from
        self._published_to = published_to

    async def read(self, reference: SourceReference) -> ReadDocument:
        """Fetch and parse one reference. A document outside the publication dates
        asked for is read and not stored: its date is known only once it is parsed.
        An exception is a failure of this document alone."""
        try:
            fetched = await self._pipeline.read(reference)
        except NoTextError:
            return ReadDocument(skipped=SkipReason.NO_TEXT)
        article = fetched.article
        skipped = _published_skip(
            article, published_from=self._published_from, published_to=self._published_to
        )
        if skipped is not None:
            return ReadDocument(skipped=skipped, published_at=article.published_at)
        return ReadDocument(published_at=article.published_at, fetched=fetched)

    async def read_many(
        self, references: Sequence[SourceReference], *, workers: int
    ) -> AsyncGenerator[tuple[SourceReference, ReadDocument | Exception]]:
        """Read independent references with at most `workers` downloads at once.

        Results come in discovery order as soon as the next one is ready, so storage
        and run accounting start with the first article and stay deterministic; no
        more than twice `workers` articles are read ahead and wait in memory. A
        document exception is returned as data: one broken page must not cancel work
        for the remaining references.
        """
        if workers < 1:
            raise ValueError("workers must be greater than zero")
        gate = asyncio.Semaphore(workers)

        async def read_one(reference: SourceReference) -> ReadDocument | Exception:
            async with gate:
                try:
                    return await self.read(reference)
                except Exception as exc:  # noqa: BLE001 - per-document isolation
                    return exc

        upcoming = iter(references)
        window: deque[tuple[SourceReference, asyncio.Task[ReadDocument | Exception]]] = deque(
            (reference, asyncio.create_task(read_one(reference)))
            for reference in islice(upcoming, 2 * workers)
        )
        try:
            while window:
                reference, task = window.popleft()
                result = await task
                following = next(upcoming, None)
                if following is not None:
                    window.append((following, asyncio.create_task(read_one(following))))
                yield reference, result
        finally:
            # Stopped early (a storage error, an interrupt): no read goes on unseen.
            for _, task in window:
                task.cancel()
            await asyncio.gather(*(task for _, task in window), return_exceptions=True)

    def store(self, document: ReadDocument) -> int:
        """Store a document that was read and not skipped; the article's id. An
        exception here is a failure of storage, not of the document."""
        if document.fetched is None:
            raise ValueError("A skipped document is not stored")
        return self._pipeline.save(document.fetched).persistence.article_id


class IngestionService:
    def __init__(
        self,
        *,
        sources: Mapping[str, SourceDefinition],
        stored: StoredDocuments,
        create_http_client: Callable[[], httpx.AsyncClient],
        create_fetcher: Callable[[], DocumentFetcher],
        create_persistence: Callable[[SourceDefinition], SqlAlchemyIngestionPersistence],
        download_workers: int = 8,
    ) -> None:
        self._sources = sources
        self._stored = stored
        self._create_http_client = create_http_client
        self._create_fetcher = create_fetcher
        self._create_persistence = create_persistence
        if download_workers < 1:
            raise ValueError("download_workers must be greater than zero")
        self._download_workers = download_workers

    def _source(self, name: str) -> SourceDefinition:
        try:
            return self._sources[name]
        except KeyError:
            raise ValueError(f"Unknown source: {name}") from None

    def source_base_url(self, source: str) -> str:
        """The address a source's stored documents are filed under. ValueError for a
        source nobody registered."""
        return self._source(source).base_url

    def known_external_ids(self, source: str, external_ids: Sequence[str]) -> set[str]:
        return self._stored.known_external_ids(
            source_base_url=self._source(source).base_url, external_ids=external_ids
        )

    async def discover(
        self, source: str, *, limit: int, stop_at_known: bool = False
    ) -> list[SourceReference]:
        """`stop_at_known`: a source that pages newest first stops where stored posts begin."""
        definition = self._source(source)
        async with self._create_http_client() as client:
            fetcher = self._create_fetcher()
            bind_http_client(fetcher, client)
            adapter = definition.create_adapter(client, fetcher)
            if stop_at_known and isinstance(adapter, DiscoversUntilKnown):
                return await adapter.discover_until_known(
                    limit=limit,
                    known=lambda external_ids: self.known_external_ids(source, list(external_ids)),
                )
            return await adapter.discover(limit=limit)

    @asynccontextmanager
    async def open(
        self,
        source: str,
        *,
        published_from: date | None = None,
        published_to: date | None = None,
    ) -> AsyncIterator[SourceIngestion]:
        """Fetch, parse and store the references of one source over one HTTP client,
        keeping only what was published within the dates given."""
        definition = self._source(source)
        async with self._create_http_client() as client:
            fetcher = self._create_fetcher()
            bind_http_client(fetcher, client)
            yield SourceIngestion(
                IngestionPipeline(
                    source_adapter=definition.create_adapter(client, fetcher),
                    parser=definition.create_parser(),
                    persistence=self._create_persistence(definition),
                ),
                published_from=published_from,
                published_to=published_to,
            )

    @property
    def download_workers(self) -> int:
        """Upper bound for fetch-and-parse workers in one source run."""
        return self._download_workers

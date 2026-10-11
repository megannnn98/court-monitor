"""The ingestion module's public API: what a caller may ask of a source.

One unit of work per call, so the caller keeps its own loop: its heartbeat, its record
of a failed document and its counters. Nothing here knows about runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Protocol

import httpx

from monitor_core.ingestion import IngestionPipeline
from monitor_core.model import SourceReference
from monitor_core.ports import DocumentFetcher
from sources.source_adapter import DiscoversUntilKnown
from sources.source_registry import SourceDefinition
from sources.sqlalchemy_persistence import (
    SqlAlchemyIngestionPersistence,
    SqlAlchemyPersistenceResult,
)


class StoredDocuments(Protocol):
    """Which documents of a source the database already holds."""

    def known_external_ids(
        self, *, source_base_url: str, external_ids: Sequence[str]
    ) -> set[str]: ...


class IngestionService:
    def __init__(
        self,
        *,
        sources: Mapping[str, SourceDefinition],
        stored: StoredDocuments,
        create_http_client: Callable[[], httpx.AsyncClient],
        create_fetcher: Callable[[], DocumentFetcher],
        create_persistence: Callable[[SourceDefinition], SqlAlchemyIngestionPersistence],
    ) -> None:
        self._sources = sources
        self._stored = stored
        self._create_http_client = create_http_client
        self._create_fetcher = create_fetcher
        self._create_persistence = create_persistence

    def _source(self, name: str) -> SourceDefinition:
        try:
            return self._sources[name]
        except KeyError:
            raise ValueError(f"Unknown source: {name}") from None

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
            adapter = definition.create_adapter(client, self._create_fetcher())
            if stop_at_known and isinstance(adapter, DiscoversUntilKnown):
                return await adapter.discover_until_known(
                    limit=limit,
                    known=lambda external_ids: self.known_external_ids(source, list(external_ids)),
                )
            return await adapter.discover(limit=limit)

    @asynccontextmanager
    async def open(
        self, source: str
    ) -> AsyncIterator[IngestionPipeline[SqlAlchemyPersistenceResult]]:
        """Fetch, parse and save the references of one source over one HTTP client.

        `read` a reference, then `save` what it gave; the saved article's id is
        `result.persistence.article_id`."""
        definition = self._source(source)
        async with self._create_http_client() as client:
            yield IngestionPipeline(
                source_adapter=definition.create_adapter(client, self._create_fetcher()),
                parser=definition.create_parser(),
                persistence=self._create_persistence(definition),
            )

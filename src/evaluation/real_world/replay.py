"""Replay the cached real-world corpus through the production source stages.

A replay source keeps the real source name, base URL and article parser; only
discovery and fetch read the local cache instead of the web. Tests and failure
scenarios inject faults per article (timeout, HTTP 500, malformed document).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

import httpx

from evaluation.real_world.corpus_cache import RawCacheEntry, RawCorpusCache
from evaluation.real_world.models import ManifestArticle, sha256_text
from sources.ingestion_errors import PermanentFetchError, TransientFetchError
from sources.models import RawDocument, SourceReference
from sources.source_adapter import DocumentFetcher, SourceAdapter
from sources.source_registry import SourceDefinition


class ReplayFault(StrEnum):
    TIMEOUT = "timeout"
    HTTP_500 = "http_500"
    MALFORMED = "malformed"


class CorpusCacheMissError(LookupError):
    """The manifest references an article the local cache does not hold."""


@dataclass
class ReplayUpstream:
    """Articles 'published' so far, per source, plus injected faults."""

    entries: Mapping[str, Mapping[str, RawCacheEntry]]
    published: dict[str, list[str]] = field(default_factory=dict)
    faults: dict[str, ReplayFault] = field(default_factory=dict)
    fetch_log: list[str] = field(default_factory=list)

    def publish(self, articles: Iterable[ManifestArticle]) -> None:
        for article in articles:
            if article.external_id not in self.entries.get(article.source, {}):
                raise CorpusCacheMissError(article.key)
            queue = self.published.setdefault(article.source, [])
            if article.external_id not in queue:
                queue.append(article.external_id)

    def published_count(self, source: str) -> int:
        return len(self.published.get(source, []))


class ReplayAdapter:
    def __init__(self, upstream: ReplayUpstream, source: str) -> None:
        self._upstream = upstream
        self._source = source

    async def discover(self, *, limit: int) -> list[SourceReference]:
        # Newest first, like a listing page.
        external_ids = list(reversed(self._upstream.published.get(self._source, [])))[:limit]
        entries = self._upstream.entries[self._source]
        return [entries[external_id].reference() for external_id in external_ids]

    async def fetch(self, reference: SourceReference) -> RawDocument:
        self._upstream.fetch_log.append(f"{self._source}:{reference.external_id}")
        fault = self._upstream.faults.get(f"{self._source}:{reference.external_id}")
        if fault is ReplayFault.TIMEOUT:
            raise TransientFetchError(f"Temporary failure fetching {reference.url}") from (
                httpx.ReadTimeout("replay timeout")
            )
        if fault is ReplayFault.HTTP_500:
            raise TransientFetchError(f"Temporary failure fetching {reference.url}: HTTP 500")
        raw = self._upstream.entries[self._source][reference.external_id].raw_document()
        if fault is ReplayFault.MALFORMED:
            return raw.model_copy(update={"content": b"<html><body>truncated"})
        return raw


class UnusedReplayFetcher:
    async def fetch(self, reference: SourceReference) -> RawDocument:
        raise PermanentFetchError("replay sources fetch through their adapter")


def replay_sources(
    upstream: ReplayUpstream, definitions: Mapping[str, SourceDefinition]
) -> dict[str, SourceDefinition]:
    def adapter_factory(
        name: str,
    ) -> Callable[[httpx.AsyncClient, DocumentFetcher], SourceAdapter]:
        def create(client: httpx.AsyncClient, fetcher: DocumentFetcher) -> SourceAdapter:
            return ReplayAdapter(upstream, name)

        return create

    return {
        name: SourceDefinition(
            name=definition.name,
            source_name=definition.source_name,
            base_url=definition.base_url,
            create_adapter=adapter_factory(name),
            create_parser=definition.create_parser,
        )
        for name, definition in definitions.items()
        if name in upstream.entries
    }


def load_replay_entries(
    cache: RawCorpusCache,
    articles: Iterable[ManifestArticle],
    definitions: Mapping[str, SourceDefinition],
) -> tuple[dict[str, dict[str, RawCacheEntry]], list[str]]:
    """Cache entries for the manifest articles; keys whose parsed text hash changed."""
    by_source: dict[str, dict[str, RawCacheEntry]] = {}
    cached: dict[str, dict[str, RawCacheEntry]] = {}
    mismatched: list[str] = []
    for article in articles:
        if article.source not in cached:
            cached[article.source] = cache.entries(article.source)
        entry = cached[article.source].get(article.external_id)
        if entry is None or entry.content_b64 is None:
            raise CorpusCacheMissError(article.key)
        parsed = definitions[article.source].create_parser().parse(entry.raw_document())
        if sha256_text(parsed.text) != article.content_hash:
            mismatched.append(article.key)
        by_source.setdefault(article.source, {})[article.external_id] = entry
    return by_source, mismatched

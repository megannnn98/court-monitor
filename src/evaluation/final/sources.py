"""An in-memory publication source for evaluation: no network, deterministic."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from sources.ingestion_errors import ParseError
from sources.models import ParsedArticle, RawDocument, SourceReference
from sources.source_adapter import DocumentFetcher, SourceAdapter
from sources.source_registry import SourceDefinition

EVALUATION_SOURCE = "ovd-info"
FIXED_FETCHED_AT = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class FixtureUpstream:
    articles: dict[str, tuple[str, str]] = field(default_factory=dict)

    def publish(self, external_id: str, title: str, text: str) -> None:
        self.articles[external_id] = (title, text)


class FixtureSourceAdapter:
    def __init__(self, upstream: FixtureUpstream) -> None:
        self._upstream = upstream

    async def discover(self, *, limit: int) -> list[SourceReference]:
        return [
            SourceReference(external_id=external_id, url=f"https://evaluation.test/{external_id}")
            for external_id in list(self._upstream.articles)[:limit]
        ]

    async def fetch(self, reference: SourceReference) -> RawDocument:
        title, text = self._upstream.articles[reference.external_id]
        return RawDocument(
            external_id=reference.external_id,
            url=reference.url,
            fetched_at=FIXED_FETCHED_AT,
            content_type="text/plain",
            content=f"{title}\n{text}".encode(),
        )


class FixtureArticleParser:
    def parse(self, raw_document: RawDocument) -> ParsedArticle:
        title, _, text = raw_document.content.decode().partition("\n")
        if not text:
            raise ParseError(f"No article text in {raw_document.external_id}")
        return ParsedArticle(
            external_id=raw_document.external_id,
            url=raw_document.url,
            title=title,
            published_at=FIXED_FETCHED_AT,
            text=text,
        )


class UnusedFetcher:
    async def fetch(self, reference: SourceReference) -> RawDocument:
        raise AssertionError("the fixture source fetches through its adapter")


def fixture_source(upstream: FixtureUpstream) -> SourceDefinition:
    def create_adapter(client: httpx.AsyncClient, fetcher: DocumentFetcher) -> SourceAdapter:
        return FixtureSourceAdapter(upstream)

    return SourceDefinition(
        name=EVALUATION_SOURCE,
        source_name="Evaluation fixture",
        base_url="https://evaluation.test",
        create_adapter=create_adapter,
        create_parser=FixtureArticleParser,
    )

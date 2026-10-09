"""`IngestionPipeline` reads a reference (fetch, parse) and saves what it read; `run` is
the two in a row. A caller with its own policy between the steps calls them apart."""

import asyncio
from datetime import UTC, datetime

import pytest

from monitor_core.errors import ParseError
from monitor_core.ingestion.pipeline import FetchedArticle, IngestionPipeline
from monitor_core.model.document import (
    IngestionResult,
    ParsedArticle,
    PersistenceResult,
    RawDocument,
)
from monitor_core.model.source import SourceReference

REFERENCE = SourceReference(external_id="a", url="https://example.test/a")
RAW = RawDocument(
    external_id="a",
    url="https://example.test/a",
    fetched_at=datetime(2026, 9, 1, tzinfo=UTC),
    content_type="text/plain",
    content=b"title\ntext",
)


class Fetcher:
    def __init__(self) -> None:
        self.fetched: list[str] = []

    async def fetch(self, reference: SourceReference) -> RawDocument:
        self.fetched.append(reference.external_id)
        return RAW


class Parser:
    def parse(self, raw: RawDocument) -> ParsedArticle:
        title, _, text = raw.content.decode().partition("\n")
        if not text:
            raise ParseError("no text")
        return ParsedArticle(
            external_id=raw.external_id, url=raw.url, title=title, published_at=None, text=text
        )


class Persistence:
    def __init__(self) -> None:
        self.saved: list[tuple[RawDocument, ParsedArticle]] = []

    def save(self, raw_document: RawDocument, article: ParsedArticle) -> PersistenceResult:
        self.saved.append((raw_document, article))
        return PersistenceResult(document_id=10, article_id=20)


def _pipeline() -> tuple[IngestionPipeline, Fetcher, Persistence]:
    fetcher, persistence = Fetcher(), Persistence()
    return IngestionPipeline(fetcher, Parser(), persistence), fetcher, persistence


def test_read_fetches_and_parses_without_saving() -> None:
    pipeline, fetcher, persistence = _pipeline()

    fetched = asyncio.run(pipeline.read(REFERENCE))

    assert fetcher.fetched == ["a"]
    assert isinstance(fetched, FetchedArticle)
    assert fetched.raw_document == RAW
    assert (fetched.article.title, fetched.article.text) == ("title", "text")
    assert persistence.saved == []


def test_save_stores_what_was_read() -> None:
    pipeline, _, persistence = _pipeline()
    fetched = asyncio.run(pipeline.read(REFERENCE))

    result = pipeline.save(fetched)

    assert persistence.saved == [(fetched.raw_document, fetched.article)]
    assert result == IngestionResult(
        article=fetched.article, persistence=PersistenceResult(document_id=10, article_id=20)
    )


def test_run_reads_then_saves() -> None:
    pipeline, fetcher, persistence = _pipeline()

    result = asyncio.run(pipeline.run(REFERENCE))

    assert fetcher.fetched == ["a"]
    assert [article for _, article in persistence.saved] == [result.article]
    assert result.persistence.article_id == 20


def test_a_parse_failure_leaves_nothing_saved() -> None:
    persistence = Persistence()

    class BrokenFetcher(Fetcher):
        async def fetch(self, reference: SourceReference) -> RawDocument:
            return RAW.model_copy(update={"content": b"title only"})

    pipeline = IngestionPipeline(BrokenFetcher(), Parser(), persistence)

    with pytest.raises(ParseError):
        asyncio.run(pipeline.run(REFERENCE))
    assert persistence.saved == []

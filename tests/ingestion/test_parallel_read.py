"""Behaviour of the bounded article-download worker pool."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ingestion.service import ReadDocument, SourceIngestion
from ingestion.sources.sqlalchemy_persistence import SqlAlchemyPersistenceResult
from monitor_core.ingestion import IngestionPipeline
from monitor_core.model import ParsedArticle, RawDocument, SourceReference


class _Fetcher:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def fetch(self, reference: SourceReference) -> RawDocument:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.01)
            if reference.external_id == "bad":
                raise RuntimeError("broken source item")
            return RawDocument(
                external_id=reference.external_id,
                url=reference.url,
                fetched_at=datetime.now(UTC),
                content_type="text/plain",
                content=b"text",
            )
        finally:
            self.active -= 1


class _Parser:
    def parse(self, raw: RawDocument) -> ParsedArticle:
        return ParsedArticle(
            external_id=raw.external_id,
            url=raw.url,
            title=raw.external_id,
            text="text",
            published_at=None,
        )


class _Persistence:
    def save(
        self, raw_document: RawDocument, article: ParsedArticle
    ) -> SqlAlchemyPersistenceResult:
        return SqlAlchemyPersistenceResult(document_id=1, article_id=1)


def _documents(fetcher: _Fetcher) -> SourceIngestion:
    return SourceIngestion(
        IngestionPipeline(fetcher, _Parser(), _Persistence()),
        published_from=None,
        published_to=None,
    )


def test_parallel_reads_are_bounded_and_isolate_a_failed_article() -> None:
    fetcher = _Fetcher()
    references = [
        SourceReference(external_id=value, url=f"https://example.test/{value}")
        for value in ("one", "bad", "two", "three")
    ]

    outcomes = asyncio.run(_collect(_documents(fetcher), references))

    assert fetcher.peak == 2
    assert [reference.external_id for reference, _ in outcomes] == ["one", "bad", "two", "three"]
    assert isinstance(outcomes[1][1], RuntimeError)
    # Every other article is read, not failed: the broken one stays its own.
    assert all(
        isinstance(result, ReadDocument)
        for reference, result in outcomes
        if reference.external_id != "bad"
    )


async def _collect(
    documents: SourceIngestion, references: list[SourceReference]
) -> list[tuple[SourceReference, ReadDocument | Exception]]:
    return [item async for item in documents.read_many(references, workers=2)]


def test_results_arrive_while_later_articles_are_still_unread() -> None:
    """Storage starts with the first article, and only a few articles wait in memory."""
    fetcher = _Fetcher()
    references = [
        SourceReference(external_id=str(value), url=f"https://example.test/{value}")
        for value in range(20)
    ]
    started: list[str] = []
    original = fetcher.fetch

    async def recording_fetch(reference: SourceReference) -> RawDocument:
        started.append(reference.external_id)
        return await original(reference)

    fetcher.fetch = recording_fetch  # type: ignore[method-assign]

    async def first_then_stop() -> int:
        documents = _documents(fetcher).read_many(references, workers=2)
        reference, _ = await anext(documents)
        assert reference.external_id == "0"
        # A slow consumer (storage): reading ahead must stop at the window.
        await asyncio.sleep(0.2)
        fetched_by_then = len(started)
        await documents.aclose()
        return fetched_by_then

    fetched_by_first = asyncio.run(first_then_stop())

    # Twice the workers ahead, plus the one queued when the first was handed over.
    assert fetched_by_first <= 5
    assert len(started) <= 5

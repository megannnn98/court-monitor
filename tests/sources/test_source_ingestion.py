import asyncio

import pytest

from sources.ingestion_errors import IngestionError
from sources.models import (
    IngestionResult,
    ParsedArticle,
    PersistenceResult,
    RawDocument,
    SourceReference,
)
from sources.source_ingestion import SourceIngestion, SourceIngestionResult

REFERENCES = [
    SourceReference(
        external_id="/express-news/a",
        url="https://ovd.info/express-news/a",
    ),
    SourceReference(
        external_id="/express-news/b",
        url="https://ovd.info/express-news/b",
    ),
    SourceReference(
        external_id="/express-news/c",
        url="https://ovd.info/express-news/c",
    ),
]


class FakeSourceAdapter:
    def __init__(self) -> None:
        self.discover_limit: int | None = None

    async def discover(
        self,
        *,
        limit: int,
    ) -> list[SourceReference]:
        self.discover_limit = limit
        return REFERENCES[:limit]

    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument:
        raise AssertionError("fetch must be called by the ingestion pipeline only")


class FakePipeline:
    def __init__(self) -> None:
        self.references: list[SourceReference] = []

    async def run(
        self,
        reference: SourceReference,
    ) -> IngestionResult:
        self.references.append(reference)

        article = ParsedArticle(
            external_id=reference.external_id,
            url=reference.url,
            title=f"Article {reference.external_id}",
            published_at=None,
            text=f"Text for {reference.external_id}",
        )

        return IngestionResult(
            article=article,
            persistence=PersistenceResult(
                document_id=len(self.references),
                article_id=len(self.references),
            ),
        )


class FailingPipeline(FakePipeline):
    async def run(
        self,
        reference: SourceReference,
    ) -> IngestionResult:
        self.references.append(reference)

        if reference.external_id == "/express-news/b":
            raise IngestionError("broken article")

        article = ParsedArticle(
            external_id=reference.external_id,
            url=reference.url,
            title=f"Article {reference.external_id}",
            published_at=None,
            text=f"Text for {reference.external_id}",
        )

        return IngestionResult(
            article=article,
            persistence=PersistenceResult(
                document_id=len(self.references),
                article_id=len(self.references),
            ),
        )


class BuggyPipeline(FakePipeline):
    async def run(
        self,
        reference: SourceReference,
    ) -> IngestionResult:
        self.references.append(reference)
        raise TypeError("programming bug")


def test_source_ingestion_discovers_and_ingests_articles_in_order() -> None:
    async def run() -> tuple[
        SourceIngestionResult,
        FakeSourceAdapter,
        FakePipeline,
    ]:
        source_adapter = FakeSourceAdapter()
        pipeline = FakePipeline()

        ingestion = SourceIngestion(
            source_adapter=source_adapter,
            pipeline=pipeline,
        )

        result = await ingestion.run(limit=3)

        return result, source_adapter, pipeline

    result, source_adapter, pipeline = asyncio.run(run())

    assert source_adapter.discover_limit == 3
    assert pipeline.references == REFERENCES

    assert [item.article.external_id for item in result.results] == [
        "/express-news/a",
        "/express-news/b",
        "/express-news/c",
    ]

    assert [item.persistence.document_id for item in result.results] == [
        1,
        2,
        3,
    ]

    assert result.failures == []


def test_source_ingestion_respects_discovery_limit() -> None:
    async def run() -> tuple[
        SourceIngestionResult,
        FakePipeline,
    ]:
        source_adapter = FakeSourceAdapter()
        pipeline = FakePipeline()

        ingestion = SourceIngestion(
            source_adapter=source_adapter,
            pipeline=pipeline,
        )

        result = await ingestion.run(limit=2)

        return result, pipeline

    result, pipeline = asyncio.run(run())

    assert pipeline.references == REFERENCES[:2]
    assert len(result.results) == 2
    assert result.failures == []


def test_source_ingestion_continues_after_ingestion_failure() -> None:
    async def run() -> tuple[
        SourceIngestionResult,
        FailingPipeline,
    ]:
        source_adapter = FakeSourceAdapter()
        pipeline = FailingPipeline()

        ingestion = SourceIngestion(
            source_adapter=source_adapter,
            pipeline=pipeline,
        )

        result = await ingestion.run(limit=3)

        return result, pipeline

    result, pipeline = asyncio.run(run())

    assert pipeline.references == REFERENCES

    assert [item.article.external_id for item in result.results] == [
        "/express-news/a",
        "/express-news/c",
    ]

    assert len(result.failures) == 1

    failure = result.failures[0]

    assert failure.reference == REFERENCES[1]
    assert isinstance(failure.error, IngestionError)
    assert str(failure.error) == "broken article"


def test_source_ingestion_does_not_swallow_unexpected_exceptions() -> None:
    async def run() -> None:
        source_adapter = FakeSourceAdapter()
        pipeline = BuggyPipeline()

        ingestion = SourceIngestion(
            source_adapter=source_adapter,
            pipeline=pipeline,
        )

        await ingestion.run(limit=1)

    with pytest.raises(TypeError, match="programming bug"):
        asyncio.run(run())

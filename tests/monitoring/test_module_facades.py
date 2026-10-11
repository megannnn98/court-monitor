"""The ingestion and processing facades over PostgreSQL with a fake upstream source.

They are not yet what `MonitoringService` calls, so this checks them against it: what
the facades store is what a monitoring run of the same source finds already done.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import (
    PETROV,
    SIDOROV,
    FakeUpstream,
    UnusedFetcher,
    build_service,
    fake_source,
    table_counts,
)

from application import build_ingestion_service, build_processing_service
from extraction.models import ExtractionRunStatus
from ingestion import IngestionService
from monitoring.models import MonitoringRunStatus

SOURCE = "ovd-info"
EXTRACTION_TABLES = (
    "source_documents",
    "parsed_articles",
    "article_extraction_runs",
    "entity_mentions",
    "extracted_events",
)


def _ingest_everything(ingestion: IngestionService) -> list[int]:
    async def run() -> list[int]:
        references = await ingestion.discover(SOURCE, limit=10)
        async with ingestion.open(SOURCE) as pipeline:
            return [
                pipeline.save(await pipeline.read(reference)).persistence.article_id
                for reference in references
            ]

    return asyncio.run(run())


def test_facades_store_what_a_monitoring_run_finds_done(
    session_factory: sessionmaker[Session],
) -> None:
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    upstream.publish("petrov", PETROV)
    source = fake_source(SOURCE, upstream)
    ingestion = build_ingestion_service(
        session_factory, sources={SOURCE: source}, create_fetcher=UnusedFetcher
    )
    processing = build_processing_service(session_factory)

    assert ingestion.known_external_ids(SOURCE, ["sidorov", "petrov"]) == set()
    article_ids = _ingest_everything(ingestion)

    assert ingestion.known_external_ids(SOURCE, ["sidorov", "petrov", "nobody"]) == {
        "sidorov",
        "petrov",
    }
    assert processing.articles_pending_extraction(source_base_url=source.base_url) == sorted(
        article_ids
    )

    saved = [processing.extract_article(article_id) for article_id in article_ids]

    assert [result.status for result in saved] == [ExtractionRunStatus.SUCCEEDED] * 2
    assert processing.articles_pending_extraction(source_base_url=source.base_url) == []
    counts = table_counts(session_factory, *EXTRACTION_TABLES)
    assert counts["parsed_articles"] == counts["article_extraction_runs"] == 2
    assert counts["entity_mentions"] > 0
    assert counts["extracted_events"] == sum(result.events_created for result in saved) > 0

    run = build_service(session_factory, {SOURCE: upstream}).run_source(
        SOURCE, with_derived=False, with_resolution=False
    )

    assert run.status is MonitoringRunStatus.COMPLETED
    assert (run.documents_discovered, run.documents_skipped, run.documents_ingested) == (2, 2, 0)
    assert run.articles_extracted == 0
    assert table_counts(session_factory, *EXTRACTION_TABLES) == counts
    assert upstream.fetches == ["sidorov", "petrov"]


def test_an_unknown_source_is_refused(session_factory: sessionmaker[Session]) -> None:
    ingestion = build_ingestion_service(session_factory, sources={})

    with pytest.raises(ValueError, match="Unknown source: nowhere"):
        ingestion.known_external_ids("nowhere", ["1"])

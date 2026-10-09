"""The OVD-Info implementations in `sources` are what the `monitor_core` ports expect,
and the names `sources` re-exports are the core's own objects, not copies."""

import asyncio

import httpx
from sqlalchemy.orm import Session, sessionmaker

import monitor_core.errors
import monitor_core.ingestion.pipeline
import monitor_core.ingestion.retry
import monitor_core.ingestion.source_ingestion
import monitor_core.model.document
import monitor_core.model.source
import monitor_core.ports.discovery
import monitor_core.ports.fetcher
import monitor_core.ports.parser
import monitor_core.ports.persistence
import sources.article_parser
import sources.ingestion_errors
import sources.ingestion_pipeline
import sources.models
import sources.persistence
import sources.retrying_fetcher
import sources.source_adapter
import sources.source_ingestion
from monitor_core.ingestion.retry import RetryingDocumentFetcher
from monitor_core.ports.discovery import SourceAdapter
from monitor_core.ports.fetcher import DocumentFetcher
from monitor_core.ports.parser import ArticleParser
from monitor_core.ports.persistence import IngestionPersistence
from sources.article_parser import OvdInfoArticleParser
from sources.ovd_info.listing_parser import OvdInfoListingParser
from sources.ovd_info.source_adapter import OvdInfoSourceAdapter
from sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence
from sources.website_adapter import WebsiteAdapter


def test_ovd_info_implementations_fit_the_core_ports() -> None:
    # The annotations are the check: mypy rejects an implementation that drifts from its port.
    client = httpx.AsyncClient()
    fetcher: DocumentFetcher = RetryingDocumentFetcher(WebsiteAdapter())
    adapter: SourceAdapter = OvdInfoSourceAdapter(
        client=client,
        listing_parser=OvdInfoListingParser(),
        document_fetcher=fetcher,
    )
    parser: ArticleParser = OvdInfoArticleParser()
    persistence: IngestionPersistence = SqlAlchemyIngestionPersistence(
        session_factory=sessionmaker[Session](),
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    )

    assert all(port is not None for port in (fetcher, adapter, parser, persistence))
    asyncio.run(client.aclose())


def test_sources_reexports_are_the_core_objects() -> None:
    reexports = [
        (sources.models, monitor_core.model.source, "SourceReference"),
        (sources.models, monitor_core.model.document, "RawDocument"),
        (sources.models, monitor_core.model.document, "ParsedArticle"),
        (sources.models, monitor_core.model.document, "PersistenceResult"),
        (sources.models, monitor_core.model.document, "IngestionResult"),
        (sources.source_adapter, monitor_core.ports.fetcher, "DocumentFetcher"),
        (sources.source_adapter, monitor_core.ports.discovery, "SourceAdapter"),
        (sources.article_parser, monitor_core.ports.parser, "ArticleParser"),
        (sources.persistence, monitor_core.ports.persistence, "IngestionPersistence"),
        (sources.ingestion_pipeline, monitor_core.ingestion.pipeline, "IngestionPipeline"),
        (sources.retrying_fetcher, monitor_core.ingestion.retry, "RetryingDocumentFetcher"),
        *(
            (sources.source_ingestion, monitor_core.ingestion.source_ingestion, name)
            for name in sources.source_ingestion.__all__
        ),
        *(
            (sources.ingestion_errors, monitor_core.errors, name)
            for name in sources.ingestion_errors.__all__
        ),
    ]

    assert [
        f"{old.__name__}.{name}"
        for old, core, name in reexports
        if getattr(old, name) is not getattr(core, name)
    ] == []

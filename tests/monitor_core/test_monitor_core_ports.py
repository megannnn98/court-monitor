"""The OVD-Info implementations in `sources` are what the `monitor_core` ports expect."""

import asyncio

import httpx
from sqlalchemy.orm import Session, sessionmaker

from monitor_core.ingestion import RetryingDocumentFetcher
from monitor_core.ports import ArticleParser, DocumentFetcher, IngestionPersistence, SourceAdapter
from sources.article_parser import OvdInfoArticleParser
from sources.ovd_info.listing_parser import OvdInfoListingParser
from sources.ovd_info.source_adapter import OvdInfoSourceAdapter
from sources.sqlalchemy_persistence import (
    SqlAlchemyIngestionPersistence,
    SqlAlchemyPersistenceResult,
)
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
    persistence: IngestionPersistence[SqlAlchemyPersistenceResult] = SqlAlchemyIngestionPersistence(
        session_factory=sessionmaker[Session](),
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    )

    assert all(port is not None for port in (fetcher, adapter, parser, persistence))
    asyncio.run(client.aclose())

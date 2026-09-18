"""Ingestion commands: one OVD-Info article, or a source's discovered documents."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable

import httpx

from cli.context import CliContext
from sources.article_parser import OvdInfoArticleParser
from sources.ingestion_pipeline import IngestionPipeline
from sources.ovd_info.reference import canonicalize_ovd_info_reference
from sources.retrying_fetcher import RetryingDocumentFetcher
from sources.source_adapter import DocumentFetcher
from sources.source_ingestion import ArticleIngestionPipeline, SourceIngestion
from sources.source_registry import OVD_INFO, SOURCES, SourceDefinition, get_source_definition
from sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence
from sources.website_adapter import WebsiteAdapter


async def discover_and_ingest(
    *,
    limit: int,
    pipeline: ArticleIngestionPipeline | None = None,
    fetcher: DocumentFetcher,
    source: SourceDefinition = OVD_INFO,
    create_pipeline: Callable[[DocumentFetcher], ArticleIngestionPipeline] | None = None,
) -> None:
    """`create_pipeline` builds the pipeline on the source's adapter, as monitoring does:
    a source may serve its documents itself (the Memorial registry does)."""
    async with httpx.AsyncClient(
        timeout=5.0,
        headers={"User-Agent": "my-app/1.0"},
    ) as client:
        source_adapter = source.create_adapter(client, fetcher)
        if create_pipeline is not None:
            pipeline = create_pipeline(source_adapter)
        if pipeline is None:
            raise ValueError("a pipeline or create_pipeline is required")

        source_ingestion = SourceIngestion(
            source_adapter=source_adapter,
            pipeline=pipeline,
        )

        result = await source_ingestion.run(limit=limit)

    for ingestion_result in result.results:
        print(
            "saved:",
            ingestion_result.article.url,
            f"document_id={ingestion_result.persistence.document_id}",
        )

    for failure in result.failures:
        print(
            "failed:",
            failure.reference.url,
            str(failure.error),
        )

    print(f"completed: {len(result.results)} saved, {len(result.failures)} failed")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Load and save an OVD-Info article",
    )
    ingest_parser.add_argument("url")
    ingest_parser.set_defaults(handler=run_ingest)
    discover_ingest_parser = subparsers.add_parser(
        "discover-and-ingest",
        help="Discover and save OVD-Info articles",
    )
    discover_ingest_parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )
    discover_ingest_parser.add_argument(
        "--source",
        choices=sorted(SOURCES),
        default=OVD_INFO.name,
    )
    discover_ingest_parser.set_defaults(handler=run_discover_and_ingest)


def run_ingest(args: argparse.Namespace, context: CliContext) -> None:
    session_factory = context.session_factory
    persistence = SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    )

    website_adapter = WebsiteAdapter()

    retrying_fetcher = RetryingDocumentFetcher(
        website_adapter,
        max_attempts=3,
        base_delay_seconds=0.5,
    )

    ingestion_pipeline = IngestionPipeline(
        source_adapter=retrying_fetcher,
        parser=OvdInfoArticleParser(),
        persistence=persistence,
    )

    reference = canonicalize_ovd_info_reference(args.url)

    if reference is None:
        raise SystemExit(f"Not a valid OVD-Info article URL: {args.url}")

    result = asyncio.run(ingestion_pipeline.run(reference))

    print("title:", result.article.title)
    print("published_at:", result.article.published_at)
    print("text:", result.article.text)
    print("document_id:", result.persistence.document_id)


def run_discover_and_ingest(args: argparse.Namespace, context: CliContext) -> None:
    session_factory = context.session_factory
    source_definition = get_source_definition(args.source)

    persistence = SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name=source_definition.source_name,
        source_base_url=source_definition.base_url,
    )

    website_adapter = WebsiteAdapter()

    retrying_fetcher = RetryingDocumentFetcher(
        website_adapter,
        max_attempts=3,
        base_delay_seconds=0.5,
    )

    asyncio.run(
        discover_and_ingest(
            limit=args.limit,
            fetcher=retrying_fetcher,
            source=source_definition,
            create_pipeline=lambda adapter: IngestionPipeline(
                source_adapter=adapter,
                parser=source_definition.create_parser(),
                persistence=persistence,
            ),
        )
    )

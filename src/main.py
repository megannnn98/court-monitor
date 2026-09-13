import argparse
import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx

from article_parser import OvdInfoArticleParser
from database import create_database_engine, create_session_factory
from evaluation_loader import load_evaluation_cases, load_evaluation_documents
from extraction_documents import SqlAlchemyExtractionDocumentRepository
from extraction_events import RuleBasedEventExtractor
from extraction_extractors import RuleBasedEntityExtractor
from extraction_metrics import evaluate_golden_dataset
from extraction_models import BatchExtractionResult, ExtractionRunStatus
from extraction_normalizers import RuleBasedMentionNormalizer
from extraction_persistence import SqlAlchemyExtractionPersistence
from extraction_pipeline import ExtractionPipeline
from ingestion_pipeline import IngestionPipeline
from models import ParsedArticle, RawDocument, SearchQuery
from ovd_info_reference import canonicalize_ovd_info_reference
from postgres_lexical_search import PostgresLexicalSearch
from retrying_fetcher import RetryingDocumentFetcher
from search_evaluator import SearchEvaluator
from source_adapter import DocumentFetcher
from source_ingestion import ArticleIngestionPipeline, SourceIngestion
from source_registry import OVD_INFO, SOURCES, SourceDefinition, get_source_definition
from sqlalchemy_persistence import SqlAlchemyIngestionPersistence
from website_adapter import WebsiteAdapter

DEFAULT_EVALUATION_CORPUS_PATH = Path("tests/fixtures/evaluation_corpus.json")
DEFAULT_EVALUATION_CASES_PATH = Path("tests/fixtures/evaluation_cases.json")
DEFAULT_EXTRACTION_CORPUS_PATH = Path("tests/fixtures/extraction_golden_corpus.json")
FIXED_EVALUATION_FETCHED_AT = datetime(2026, 1, 1, tzinfo=UTC)


async def discover_and_ingest(
    *,
    limit: int,
    pipeline: ArticleIngestionPipeline,
    fetcher: DocumentFetcher,
    source: SourceDefinition = OVD_INFO,
) -> None:
    async with httpx.AsyncClient(
        timeout=5.0,
        headers={"User-Agent": "my-app/1.0"},
    ) as client:
        source_adapter = source.create_adapter(client, fetcher)

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


def main() -> None:
    argument_parser = argparse.ArgumentParser(description="Ingest and search OVD-Info articles")
    subparsers = argument_parser.add_subparsers(
        dest="command",
        required=True,
    )

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Load and save an OVD-Info article",
    )
    ingest_parser.add_argument("url")

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

    search_parser = subparsers.add_parser(
        "search",
        help="Search saved articles",
    )
    search_parser.add_argument("text")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )

    evaluate_search_parser = subparsers.add_parser(
        "evaluate-search",
        help="Evaluate lexical search against fixed cases",
    )
    evaluate_search_parser.add_argument(
        "--corpus-path",
        type=Path,
        default=DEFAULT_EVALUATION_CORPUS_PATH,
    )
    evaluate_search_parser.add_argument(
        "--cases-path",
        type=Path,
        default=DEFAULT_EVALUATION_CASES_PATH,
    )
    evaluate_search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )
    evaluate_search_parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
    )
    extract_entities_parser = subparsers.add_parser(
        "extract-entities",
        help="Extract entity mentions and events from saved articles",
    )
    extract_entities_parser.add_argument("--article-id", type=int, default=None)
    extract_entities_parser.add_argument("--source", choices=sorted(SOURCES), default=None)
    extract_entities_parser.add_argument("--limit", type=int, default=100)

    evaluate_extraction_parser = subparsers.add_parser(
        "evaluate-extraction",
        help="Evaluate extraction against a fixed golden corpus",
    )
    evaluate_extraction_parser.add_argument(
        "--corpus-path",
        type=Path,
        default=DEFAULT_EXTRACTION_CORPUS_PATH,
    )
    evaluate_extraction_parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
    )

    args = argument_parser.parse_args()

    if args.command == "evaluate-extraction":
        extraction_report = evaluate_golden_dataset(args.corpus_path)
        report_json = extraction_report.model_dump_json(indent=2)
        if args.output_path is not None:
            args.output_path.parent.mkdir(parents=True, exist_ok=True)
            args.output_path.write_text(report_json + "\n", encoding="utf-8")
        else:
            print(report_json)
        return

    database_url = os.environ.get("DATABASE_URL")

    if database_url is None:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    database_engine = create_database_engine(database_url)
    session_factory = create_session_factory(database_engine)

    if args.command == "search":
        search = PostgresLexicalSearch(session_factory)

        hits = search.search(
            SearchQuery(
                text=args.text,
                limit=args.limit,
            )
        )

        for hit in hits:
            print(f"[{hit.score:.4f}] {hit.title}")
            print(hit.url)
            print(hit.text)
            print()

        return

    if args.command == "evaluate-search":
        documents = load_evaluation_documents(args.corpus_path)

        persistence = SqlAlchemyIngestionPersistence(
            session_factory=session_factory,
            source_name="ОВД-Инфо evaluation",
            source_base_url="https://ovd.info",
        )

        for document in documents:
            raw_document = RawDocument(
                external_id=document.external_id,
                url=document.canonical_url,
                fetched_at=FIXED_EVALUATION_FETCHED_AT,
                content_type="text/plain",
                content=document.title.encode("utf-8"),
            )

            article = ParsedArticle(
                external_id=document.external_id,
                url=document.canonical_url,
                title=document.title,
                published_at=None,
                text=document.text,
            )

            persistence.save(
                raw_document=raw_document,
                article=article,
            )

        cases = load_evaluation_cases(args.cases_path)

        search = PostgresLexicalSearch(session_factory)

        evaluator = SearchEvaluator(
            search=search,
            limit=args.limit,
        )

        report = evaluator.evaluate(cases)

        report_json = report.model_dump_json(indent=2)

        if args.output_path is not None:
            args.output_path.parent.mkdir(parents=True, exist_ok=True)
            args.output_path.write_text(report_json + "\n", encoding="utf-8")
        else:
            print(report_json)

        return

    if args.command == "extract-entities":
        document_repository = SqlAlchemyExtractionDocumentRepository(session_factory)
        extraction_persistence = SqlAlchemyExtractionPersistence(session_factory)
        extraction_pipeline = ExtractionPipeline(
            extractors=[RuleBasedEntityExtractor()],
            normalizers=[RuleBasedMentionNormalizer()],
            event_extractor=RuleBasedEventExtractor(),
            persistence=extraction_persistence,
        )
        if args.article_id is not None:
            extraction_documents = [document_repository.get_by_article_id(args.article_id)]
        else:
            source_name = (
                get_source_definition(args.source).source_name if args.source is not None else None
            )
            extraction_documents = document_repository.list_documents(
                source_name=source_name,
                limit=args.limit,
            )

        batch_result = BatchExtractionResult()
        for extraction_document in extraction_documents:
            save_result = extraction_pipeline.run(extraction_document)
            if save_result.status is ExtractionRunStatus.SUCCEEDED:
                if save_result.skipped_existing:
                    batch_result.articles_skipped += 1
                else:
                    batch_result.articles_processed += 1
                batch_result.mentions_created += save_result.mentions_created
                batch_result.events_created += save_result.events_created
            else:
                batch_result.articles_failed += 1
                batch_result.failures.append(
                    f"article_id={extraction_document.article_id}: {save_result.error_message}"
                )
        print(batch_result.model_dump_json(indent=2))
        return

    if args.command == "discover-and-ingest":
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

        ingestion_pipeline = IngestionPipeline(
            source_adapter=retrying_fetcher,
            parser=source_definition.create_parser(),
            persistence=persistence,
        )

        asyncio.run(
            discover_and_ingest(
                limit=args.limit,
                pipeline=ingestion_pipeline,
                fetcher=retrying_fetcher,
                source=source_definition,
            )
        )
        return

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


if __name__ == "__main__":
    main()

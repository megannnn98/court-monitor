import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from datetime import UTC, date, datetime, time
from pathlib import Path

import httpx
from sqlalchemy.exc import NoResultFound

from candidates.service import CandidateQueryService
from db.database import create_database_engine, create_session_factory
from evaluation.final.cli import add_final_evaluation_arguments, run_final_evaluation_command
from evaluation.real_world.cli import add_real_world_arguments, run_real_world_command
from extraction.documents import SqlAlchemyExtractionDocumentRepository
from extraction.events import RuleBasedEventExtractor
from extraction.extractors import RuleBasedEntityExtractor
from extraction.metrics import evaluate_golden_dataset
from extraction.models import BatchExtractionResult, ExtractionRunStatus
from extraction.normalizers import RuleBasedMentionNormalizer
from extraction.parallel_resolution import resolve_runs
from extraction.persistence import SqlAlchemyExtractionPersistence
from extraction.pipeline import ExtractionPipeline
from monitoring.cli import add_monitoring_arguments, run_monitoring_command
from persecution.classification_service import PersecutionClassificationService
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.cli import (
    add_person_resolution_arguments,
    run_evaluate_er,
    run_person_resolution_command,
)
from research.cli import (
    ResearchCliError,
    add_ask_arguments,
    add_research_arguments,
    ask_exit_code,
    build_research_request,
    format_query_result,
    format_research_plan,
    format_research_response,
    format_structured_request,
)
from research.repository import SqlAlchemyPersonResearchRepository
from research.service import ResearchService, ResearchSnapshotNotFoundError
from research.workflow.graph import run_research_query
from research.workflow.llm import LlmConfigurationError
from research.workflow_factory import create_research_graph
from rosfinmonitoring.ingestion import RosfinmonitoringIngestionPipeline
from rosfinmonitoring.matcher import RuleBasedRosfinmonitoringMatcher
from rosfinmonitoring.matcher_persistence import RosfinMatchPersistence
from rosfinmonitoring.persistence import RosfinmonitoringPersistence
from search.evaluation_loader import load_evaluation_cases, load_evaluation_documents
from search.evaluator import SearchEvaluator
from search.postgres_lexical import PostgresLexicalSearch
from semantic_retrieval.cli import (
    add_semantic_arguments,
    run_evaluate_retrieval,
    run_semantic_command,
)
from semantic_retrieval.models import SemanticConfigurationError
from settings import ApplicationConfigurationError, ApplicationSettings
from sources.article_parser import OvdInfoArticleParser
from sources.ingestion_pipeline import IngestionPipeline
from sources.models import ParsedArticle, RawDocument, SearchQuery
from sources.ovd_info.reference import canonicalize_ovd_info_reference
from sources.retrying_fetcher import RetryingDocumentFetcher
from sources.source_adapter import DocumentFetcher
from sources.source_ingestion import ArticleIngestionPipeline, SourceIngestion
from sources.source_registry import OVD_INFO, SOURCES, SourceDefinition, get_source_definition
from sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence
from sources.website_adapter import WebsiteAdapter

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

    resolve_people_parser = subparsers.add_parser(
        "resolve-people",
        help="Resolve extracted person mentions to canonical persons",
    )
    resolve_people_parser.add_argument(
        "--article-id",
        type=int,
        default=None,
        help="Resolve mentions from a specific article",
    )
    resolve_people_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of articles to process",
    )
    resolve_people_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Resolve articles in this many worker processes",
    )

    import_rosfin_parser = subparsers.add_parser(
        "import-rosfinmonitoring",
        help="Import a Rosfinmonitoring list snapshot from a downloaded file",
    )
    import_rosfin_parser.add_argument("--file", type=Path, required=True)
    import_rosfin_parser.add_argument(
        "--source-url",
        required=True,
        help="Where the file was downloaded from; stored with the snapshot",
    )
    import_rosfin_parser.add_argument(
        "--snapshot-date",
        type=date.fromisoformat,
        default=None,
        help="Publication date of the list (YYYY-MM-DD); defaults to now",
    )

    match_rosfin_parser = subparsers.add_parser(
        "match-rosfinmonitoring",
        help="Match canonical persons against Rosfinmonitoring entries",
    )
    match_rosfin_parser.add_argument(
        "--snapshot-id",
        type=int,
        required=True,
        help="Rosfinmonitoring snapshot ID to match against",
    )
    match_rosfin_parser.add_argument(
        "--person-id",
        type=int,
        default=None,
        help="Match a specific person",
    )
    match_rosfin_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of persons to process",
    )

    classify_persecution_parser = subparsers.add_parser(
        "classify-persecution",
        help="Classify persons for political persecution",
    )
    classify_persecution_parser.add_argument(
        "--person-id",
        type=int,
        default=None,
        help="Classify a specific person",
    )
    classify_persecution_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of persons to process",
    )

    list_candidates_parser = subparsers.add_parser(
        "list-candidates",
        help="List politically persecuted persons absent from Rosfinmonitoring",
    )
    list_candidates_parser.add_argument(
        "--snapshot-id",
        type=int,
        required=True,
        help="Rosfinmonitoring snapshot ID to check against",
    )
    list_candidates_parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.7,
        help="Minimum persecution confidence threshold",
    )
    list_candidates_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of candidates to return",
    )
    list_candidates_parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Output file path (JSON format)",
    )

    add_research_arguments(subparsers)
    add_ask_arguments(subparsers)
    add_semantic_arguments(subparsers)
    add_person_resolution_arguments(subparsers)
    add_monitoring_arguments(subparsers)
    add_final_evaluation_arguments(subparsers)
    add_real_world_arguments(subparsers)

    subparsers.add_parser(
        "validate-config",
        help="Validate the whole configuration and print it without secrets",
    )

    args = argument_parser.parse_args()

    if args.command == "validate-config":
        try:
            settings = ApplicationSettings.from_env(require_database=False)
        except ApplicationConfigurationError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from None
        print(json.dumps(settings.redacted(), indent=2, ensure_ascii=False))
        return

    if args.command == "evaluate-extraction":
        extraction_report = evaluate_golden_dataset(args.corpus_path)
        report_json = extraction_report.model_dump_json(indent=2)
        if args.output_path is not None:
            args.output_path.parent.mkdir(parents=True, exist_ok=True)
            args.output_path.write_text(report_json + "\n", encoding="utf-8")
        else:
            print(report_json)
        return

    if args.command == "evaluate-retrieval":
        # Uses its own disposable database, never DATABASE_URL.
        run_evaluate_retrieval(args)
        return

    if args.command == "evaluate-final":
        # Uses its own disposable database, never DATABASE_URL.
        run_final_evaluation_command(args)
        return

    # Real-world validation: own disposable database / local cache, never DATABASE_URL.
    if run_real_world_command(args):
        return
    if args.command == "evaluate-er":
        # Uses its own disposable database, never DATABASE_URL.
        run_evaluate_er(args)
        return

    # Fail fast with every configuration problem, before touching the database.
    try:
        settings = ApplicationSettings.from_env()
    except ApplicationConfigurationError as exc:
        raise SystemExit(str(exc)) from None

    database_engine = create_database_engine(settings.database_url, settings.database_pool)
    session_factory = create_session_factory(database_engine)

    if run_semantic_command(args, session_factory):
        return

    if run_person_resolution_command(args, session_factory):
        return

    if run_monitoring_command(args, session_factory):
        return

    if args.command == "list-candidates":
        service = CandidateQueryService(session_factory)
        candidates_result = service.get_candidates(
            snapshot_id=args.snapshot_id,
            min_persecution_confidence=args.min_confidence,
            limit=args.limit,
        )
        report_json = candidates_result.model_dump_json(indent=2)
        if args.output_path is not None:
            args.output_path.parent.mkdir(parents=True, exist_ok=True)
            args.output_path.write_text(report_json + "\n", encoding="utf-8")
        else:
            print(report_json)
        return

    if args.command == "ask":
        logging.basicConfig(
            level=logging.INFO if args.verbose else logging.WARNING,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        try:
            research_graph = create_research_graph(session_factory)
        except (LlmConfigurationError, SemanticConfigurationError) as exc:
            raise SystemExit(str(exc)) from None
        query_result = run_research_query(research_graph, args.query)
        if args.show_request:
            print(format_structured_request(query_result))
        if args.show_plan:
            print(format_research_plan(query_result))
        if args.json:
            print(query_result.model_dump_json(indent=2))
        else:
            print(format_query_result(query_result, raw=args.raw))
        exit_code = ask_exit_code(query_result)
        if exit_code:
            raise SystemExit(exit_code)
        return

    if args.command == "research":
        try:
            research_request = build_research_request(args)
        except ResearchCliError as exc:
            raise SystemExit(str(exc)) from None
        research_service = ResearchService(
            repository=SqlAlchemyPersonResearchRepository(session_factory),
            candidate_query=CandidateQueryService(session_factory),
        )
        try:
            research_response = research_service.execute(research_request)
        except ResearchSnapshotNotFoundError as exc:
            raise SystemExit(str(exc)) from None
        if args.json:
            print(research_response.model_dump_json(indent=2))
        else:
            print(format_research_response(research_response))
        return

    if args.command == "resolve-people":
        document_repository = SqlAlchemyExtractionDocumentRepository(session_factory)
        extraction_persistence = SqlAlchemyExtractionPersistence(session_factory)
        if args.article_id is not None:
            extraction_documents = [document_repository.get_by_article_id(args.article_id)]
        else:
            extraction_documents = document_repository.list_documents(
                source_name=None,
                limit=args.limit,
            )

        run_ids = []
        for doc in extraction_documents:
            run_id = extraction_persistence.get_latest_run_by_article_id(doc.article_id)
            if run_id is None:
                print(f"No extraction run found for article {doc.article_id}, skipping")
                continue
            run_ids.append(run_id)

        totals = resolve_runs(settings.database_url, run_ids, workers=args.workers)

        print(
            f"Resolved {totals.mentions_resolved} mentions, "
            f"created {totals.new_persons_created} persons, "
            f"linked {totals.events_linked} events, "
            f"{totals.reviews_pending} mentions pending person resolution review"
        )
        return

    if args.command == "import-rosfinmonitoring":
        snapshot_date = (
            datetime.combine(args.snapshot_date, time.min, UTC)
            if args.snapshot_date is not None
            else None
        )
        ingestion = RosfinmonitoringIngestionPipeline(RosfinmonitoringPersistence(session_factory))
        try:
            ingestion_result = ingestion.ingest_from_file(
                str(args.file), source_url=args.source_url, snapshot_date=snapshot_date
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        print(
            f"snapshot {ingestion_result.snapshot_id}: "
            f"{ingestion_result.entries_created} entries created, "
            f"{ingestion_result.entries_updated} updated, "
            f"{ingestion_result.skipped_duplicates} duplicates skipped"
        )
        return

    if args.command == "match-rosfinmonitoring":
        person_persistence = SqlAlchemyPersonPersistence(session_factory)
        matcher = RuleBasedRosfinmonitoringMatcher(session_factory)
        match_persistence = RosfinMatchPersistence(session_factory)

        if args.person_id is not None:
            match_result = matcher.match_person(
                person_id=args.person_id,
                snapshot_id=args.snapshot_id,
            )
            match_persistence.save_match_result(match_result)
            print(f"Matched person {args.person_id}: {match_result.status}")
            print(f"Confidence: {match_result.confidence:.2f}")
            if match_result.matched_entry_id:
                print(f"Matched entry ID: {match_result.matched_entry_id}")
        else:
            results = matcher.match_all_persons(
                snapshot_id=args.snapshot_id,
                limit=args.limit,
            )
            status_counts: Counter[str] = Counter()
            for match_result_item in results:
                match_persistence.save_match_result(match_result_item)
                status_counts[match_result_item.status.value] += 1

            breakdown = ", ".join(
                f"{count} {status}" for status, count in sorted(status_counts.items())
            )
            print(f"Matched {len(results)} persons: {breakdown}")
        return

    if args.command == "classify-persecution":
        person_persistence = SqlAlchemyPersonPersistence(session_factory)
        classification_service = PersecutionClassificationService(session_factory)

        if args.person_id is not None:
            person = person_persistence.get_person(args.person_id)
            if person is None:
                raise SystemExit(f"Person not found: {args.person_id}")

            classification = classification_service.classify_person(person.id)
            print(f"Classified person {person.id}: {classification.status}")
            print(f"Confidence: {classification.confidence:.2f}")
            if classification.reasons:
                print("Reasons:")
                for reason in classification.reasons:
                    print(f"  - {reason}")
        else:
            persons = person_persistence.list_active_persons(limit=args.limit)
            classified_count = 0
            political_count = 0

            for person in persons:
                classification = classification_service.classify_person(person.id)
                classified_count += 1
                if classification.status == "political":
                    political_count += 1

            print(f"Classified {classified_count} persons: {political_count} political persecution")
        return

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
            try:
                extraction_documents = [document_repository.get_by_article_id(args.article_id)]
            except NoResultFound:
                raise SystemExit(f"Article not found: {args.article_id}") from None
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

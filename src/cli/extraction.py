"""Entity and event extraction, and its golden-corpus evaluation."""

from __future__ import annotations

import argparse
from functools import partial
from pathlib import Path

from sqlalchemy.exc import NoResultFound

from cli.context import CliContext
from cli_batches import (
    DEFAULT_WORKERS,
    MAX_GPU_WORKERS,
    extract_articles,
    extraction_uses_gpu,
    merge_extraction_results,
    run_chunks,
    worker_count,
)
from cli_progress import ProgressBar
from extraction.documents import SqlAlchemyExtractionDocumentRepository
from extraction.metrics import evaluate_golden_dataset
from sources.source_registry import SOURCES, get_source_definition

DEFAULT_EXTRACTION_CORPUS_PATH = Path("tests/fixtures/extraction_golden_corpus.json")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    extract_entities_parser = subparsers.add_parser(
        "extract-entities",
        help="Extract entity mentions and events from saved articles",
    )
    extract_entities_parser.add_argument("--article-id", type=int, default=None)
    extract_entities_parser.add_argument("--source", choices=sorted(SOURCES), default=None)
    extract_entities_parser.add_argument("--limit", type=int, default=100)
    extract_entities_parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            "Extract articles in this many worker processes, at most "
            f"{MAX_GPU_WORKERS} with the person recognizer on the GPU"
        ),
    )
    extract_entities_parser.set_defaults(handler=run_extract_entities)
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
    evaluate_extraction_parser.set_defaults(handler=run_evaluate_extraction)


def run_extract_entities(args: argparse.Namespace, context: CliContext) -> None:
    settings = context.settings
    database_engine = context.engine
    session_factory = context.session_factory
    document_repository = SqlAlchemyExtractionDocumentRepository(session_factory)
    if args.article_id is not None:
        try:
            document_repository.get_by_article_id(args.article_id)
        except NoResultFound:
            raise SystemExit(f"Article not found: {args.article_id}") from None
        article_ids = [args.article_id]
    else:
        source_name = (
            get_source_definition(args.source).source_name if args.source is not None else None
        )
        article_ids = [
            document.article_id
            for document in document_repository.list_documents(
                source_name=source_name,
                limit=args.limit,
            )
        ]

    extract_workers = worker_count(args.workers, len(article_ids), uses_gpu=extraction_uses_gpu())
    if extract_workers > 1:
        database_engine.dispose()
    with ProgressBar("extract-entities", len(article_ids)) as progress:
        batch_result = merge_extraction_results(
            run_chunks(
                "extract-entities",
                partial(extract_articles, settings.database_url),
                article_ids,
                workers=extract_workers,
                on_progress=progress.advance,
            )
        )
    print(batch_result.model_dump_json(indent=2))


def run_evaluate_extraction(args: argparse.Namespace, context: CliContext) -> None:
    extraction_report = evaluate_golden_dataset(args.corpus_path)
    report_json = extraction_report.model_dump_json(indent=2)
    if args.output_path is not None:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(report_json + "\n", encoding="utf-8")
    else:
        print(report_json)

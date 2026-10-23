"""Entity and event extraction, and its golden-corpus evaluation."""

from __future__ import annotations

import argparse
import json
import logging
from functools import partial
from pathlib import Path

from sqlalchemy.exc import NoResultFound

from cli.context import CliContext
from cli_batches import (
    DEFAULT_WORKERS,
    current_extraction_versions,
    extract_articles,
    merge_extraction_results,
    run_chunks,
    worker_count,
)
from cli_progress import ProgressBar
from ingestion.sources.source_registry import SOURCES, get_source_definition
from processing.extraction.documents import SqlAlchemyExtractionDocumentRepository
from processing.extraction.metrics import evaluate_golden_dataset
from processing.extraction.reprocessing import plan_reprocessing, stale_articles

logger = logging.getLogger("reprocess")

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
        default=None,
        help=f"Extract articles in this many worker processes (default {DEFAULT_WORKERS}; "
        "1 with --stale)",
    )
    extract_entities_parser.add_argument(
        "--stale",
        action="store_true",
        help="Re-extract stored articles that have no run under the current versions, in id "
        "order, --limit at a time. Older runs are kept. Run again for the next batch",
    )
    extract_entities_parser.add_argument(
        "--article-ids",
        type=_article_ids,
        default=None,
        help="With --stale: only these articles (comma-separated ids)",
    )
    extract_entities_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --stale: print what would be read and what it leaves behind; write nothing",
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


def _article_ids(value: str) -> list[int]:
    try:
        return [int(part) for part in value.split(",") if part.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a list of article ids: {value!r}") from None


def _stale_article_ids(args: argparse.Namespace, context: CliContext) -> list[int]:
    """The next batch of the stored articles not yet read by the current versions."""
    versions = current_extraction_versions(context.session_factory)
    plan = plan_reprocessing(
        context.session_factory, versions, article_ids=args.article_ids, limit=args.limit
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "versions": versions._asdict(),
                    "stale_articles_total": plan.stale_total,
                    "selected_count": len(plan.selected),
                    "selected_first_ids": list(plan.selected[:20]),
                    "selected_without_text": plan.selected_without_text,
                    "articles_with_several_runs": plan.articles_with_several_runs,
                    "group_mentions_on_older_runs": plan.group_mentions_on_older_runs,
                    "person_event_links": plan.person_event_links,
                    "stages": ["extract-entities (entities, events); no resolution, no collect"],
                    "warnings": list(plan.warnings),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(0)
    logger.info(
        "event=reprocess_batch versions=%s stale_total=%d selected=%d first=%s",
        "/".join(versions),
        plan.stale_total,
        len(plan.selected),
        list(plan.selected[:5]),
    )
    return list(plan.selected)


def run_extract_entities(args: argparse.Namespace, context: CliContext) -> None:
    settings = context.settings
    database_engine = context.engine
    session_factory = context.session_factory
    if (args.article_ids is not None or args.dry_run) and not args.stale:
        raise SystemExit("--article-ids and --dry-run need --stale")
    if args.stale and (args.article_id is not None or args.source is not None):
        raise SystemExit("--stale cannot be combined with --article-id or --source")
    document_repository = SqlAlchemyExtractionDocumentRepository(session_factory)
    if args.stale:
        article_ids = _stale_article_ids(args, context)
    elif args.article_id is not None:
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

    requested_workers = args.workers or (1 if args.stale else DEFAULT_WORKERS)
    extract_workers = worker_count(requested_workers, len(article_ids))
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
    if args.stale:
        left = len(stale_articles(session_factory, current_extraction_versions(session_factory)))
        logger.info(
            "event=reprocess_batch_done processed=%d skipped=%d failed=%d stale_left=%d",
            batch_result.articles_processed,
            batch_result.articles_skipped,
            batch_result.articles_failed,
            left,
        )
        print(f"stale articles left: {left}")
        if batch_result.articles_failed:
            # After the report: a failed article stays stale, so a rerun is not a finished job.
            raise SystemExit(1)


def run_evaluate_extraction(args: argparse.Namespace, context: CliContext) -> None:
    extraction_report = evaluate_golden_dataset(args.corpus_path)
    report_json = extraction_report.model_dump_json(indent=2)
    if args.output_path is not None:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(report_json + "\n", encoding="utf-8")
    else:
        print(report_json)

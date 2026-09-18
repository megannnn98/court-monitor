"""Lexical search and its evaluation."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from cli.context import CliContext
from search.evaluation_loader import load_evaluation_cases, load_evaluation_documents
from search.evaluator import SearchEvaluator
from search.postgres_lexical import PostgresLexicalSearch
from sources.models import ParsedArticle, RawDocument, SearchQuery
from sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence

DEFAULT_EVALUATION_CORPUS_PATH = Path("tests/fixtures/evaluation_corpus.json")
DEFAULT_EVALUATION_CASES_PATH = Path("tests/fixtures/evaluation_cases.json")
FIXED_EVALUATION_FETCHED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
    search_parser.set_defaults(handler=run_search)
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
    evaluate_search_parser.set_defaults(handler=run_evaluate_search)


def run_search(args: argparse.Namespace, context: CliContext) -> None:
    session_factory = context.session_factory
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


def run_evaluate_search(args: argparse.Namespace, context: CliContext) -> None:
    session_factory = context.session_factory
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

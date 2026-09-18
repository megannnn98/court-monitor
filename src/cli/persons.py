"""Person resolution over extracted mentions."""

from __future__ import annotations

import argparse

from cli.context import CliContext
from cli_progress import ProgressBar
from extraction.documents import SqlAlchemyExtractionDocumentRepository
from extraction.parallel_resolution import resolve_runs
from extraction.persistence import SqlAlchemyExtractionPersistence


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
        help=(
            "Resolve articles in this many worker processes (bulk rebuild only: "
            "decisions may differ from a single process)"
        ),
    )
    resolve_people_parser.set_defaults(handler=run_resolve_people)


def run_resolve_people(args: argparse.Namespace, context: CliContext) -> None:
    settings = context.settings
    database_engine = context.engine
    session_factory = context.session_factory
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

    if args.workers > 1:
        # Worker processes open their own connections; this one's pool must not be
        # inherited by a fork.
        database_engine.dispose()
    with ProgressBar("resolve-people", len(run_ids)) as progress:
        totals = resolve_runs(
            settings.database_url,
            run_ids,
            workers=args.workers,
            on_progress=progress.advance,
        )

    print(
        f"Resolved {totals.mentions_resolved} mentions, "
        f"created {totals.new_persons_created} persons, "
        f"linked {totals.events_linked} events, "
        f"{totals.reviews_pending} mentions pending person resolution review"
    )

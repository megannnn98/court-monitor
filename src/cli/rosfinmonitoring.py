"""Rosfinmonitoring list snapshots and matching persons against them."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, date, datetime, time
from functools import partial
from pathlib import Path

from cli.context import CliContext
from cli_batches import (
    DEFAULT_WORKERS,
    match_persons,
    run_chunks,
    worker_count,
)
from cli_progress import ProgressBar
from rosfinmonitoring.ingestion import RosfinmonitoringIngestionPipeline
from rosfinmonitoring.matcher import RuleBasedRosfinmonitoringMatcher
from rosfinmonitoring.matcher_persistence import RosfinMatchPersistence
from rosfinmonitoring.persistence import RosfinmonitoringPersistence


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
    import_rosfin_parser.set_defaults(handler=run_import_rosfinmonitoring)
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
    match_rosfin_parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="Match persons in this many worker processes",
    )
    match_rosfin_parser.set_defaults(handler=run_match_rosfinmonitoring)


def run_import_rosfinmonitoring(args: argparse.Namespace, context: CliContext) -> None:
    session_factory = context.session_factory
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


def run_match_rosfinmonitoring(args: argparse.Namespace, context: CliContext) -> None:
    settings = context.settings
    database_engine = context.engine
    session_factory = context.session_factory
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
        person_ids = matcher.person_ids_to_match(limit=args.limit)
        match_workers = worker_count(args.workers, len(person_ids))
        if match_workers > 1:
            database_engine.dispose()
        status_counts: Counter[str] = Counter()
        with ProgressBar("match-rosfinmonitoring", len(person_ids)) as progress:
            for chunk_counts in run_chunks(
                "match-rosfinmonitoring",
                partial(match_persons, settings.database_url, args.snapshot_id),
                person_ids,
                workers=match_workers,
                on_progress=progress.advance,
            ):
                status_counts.update(chunk_counts)

        breakdown = ", ".join(
            f"{count} {status}" for status, count in sorted(status_counts.items())
        )
        print(f"Matched {status_counts.total()} persons: {breakdown}")

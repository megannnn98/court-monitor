"""Event clusters: group the publications that tell one event."""

from __future__ import annotations

import argparse
import dataclasses
import json

from cases.clusters.embeddings import TextSimilarity
from cases.clusters.service import resolve
from cli.context import CliContext


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "resolve-clusters",
        help=(
            "Group the publications that tell one event into clusters. The first run "
            "clusters everything accumulated, later runs take what is new; both are safe "
            "to repeat"
        ),
    )
    parser.add_argument(
        "--batch-size", type=int, default=500, help="Events per transaction (default 500)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would be done and write nothing"
    )
    parser.add_argument(
        "--text-similarity",
        action="store_true",
        help=(
            "Add the e5 similarity of the quotes as a ranking signal (off by default; "
            "the vectors are kept in a cache file outside the database, never in it)"
        ),
    )
    parser.set_defaults(handler=run_resolve_clusters)


def run_resolve_clusters(args: argparse.Namespace, context: CliContext) -> None:
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    similarity = TextSimilarity(enabled=bool(args.text_similarity))
    report = resolve(
        context.session_factory,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        text_similarity=similarity if similarity.enabled else None,
    )
    similarity.save()
    print(json.dumps(dataclasses.asdict(report), ensure_ascii=False))

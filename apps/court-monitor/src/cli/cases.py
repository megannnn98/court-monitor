"""Criminal case resolution: tie the extracted events to cases."""

from __future__ import annotations

import argparse
import dataclasses
import json

from cases.service import resolve
from cli.context import CliContext


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "resolve-cases",
        help=(
            "Tie the extracted events to criminal cases. The first run forms the cases of "
            "everything accumulated, later runs take what is new; both are safe to repeat"
        ),
    )
    parser.add_argument(
        "--batch-size", type=int, default=500, help="Events per transaction (default 500)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would be done and write nothing"
    )
    parser.set_defaults(handler=run_resolve_cases)


def run_resolve_cases(args: argparse.Namespace, context: CliContext) -> None:
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    report = resolve(context.session_factory, batch_size=args.batch_size, dry_run=args.dry_run)
    print(json.dumps(dataclasses.asdict(report), ensure_ascii=False))

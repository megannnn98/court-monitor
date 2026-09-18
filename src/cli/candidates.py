"""The main product query: politically persecuted persons absent from Rosfinmonitoring."""

from __future__ import annotations

import argparse
from pathlib import Path

from candidates.service import CandidateQueryService
from cli.context import CliContext


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
    list_candidates_parser.set_defaults(handler=run_list_candidates)


def run_list_candidates(args: argparse.Namespace, context: CliContext) -> None:
    session_factory = context.session_factory
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

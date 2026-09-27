"""The CLI parser, assembled from every area's `register`, and the dispatch to handlers."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from cli import (
    areas,
    candidates,
    config,
    extraction,
    ingestion,
    persecution,
    persons,
    rosfinmonitoring,
)
from cli.context import CliContext

# In the order the commands were registered before the split: it is the order of `--help`.
_REGISTRATIONS = (
    ingestion.register,
    extraction.register,
    persons.register,
    rosfinmonitoring.register,
    persecution.register,
    candidates.register,
    areas.register_person_resolution,
    areas.register_monitoring,
    areas.register_evaluation,
    config.register,
)


def build_parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Ingest OVD-Info articles and find criminal cases"
    )
    subparsers = argument_parser.add_subparsers(
        dest="command",
        required=True,
    )
    for register in _REGISTRATIONS:
        register(subparsers)
    return argument_parser


def main(argv: Sequence[str] | None = None, *, context: CliContext | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.handler(args, context or CliContext())

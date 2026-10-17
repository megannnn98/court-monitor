"""Configuration check: every setting validated and printed without secrets."""

from __future__ import annotations

import argparse
import json
import sys

from cli.context import CliContext
from settings import ApplicationConfigurationError, ApplicationSettings


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    validate_config_parser = subparsers.add_parser(
        "validate-config",
        help="Validate the whole configuration and print it without secrets",
    )
    validate_config_parser.set_defaults(handler=run_validate_config)


def run_validate_config(args: argparse.Namespace, context: CliContext) -> None:
    try:
        settings = ApplicationSettings.from_env(require_database=False)
    except ApplicationConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(settings.redacted(), indent=2, ensure_ascii=False))

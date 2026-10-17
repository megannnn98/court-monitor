"""Areas whose commands are defined and run by their own `cli` module: person-resolution
review and monitoring. The evaluations are tooling, with their own command line
(`court-evaluation`, `evaluation.cli`).
"""

from __future__ import annotations

import argparse

from cli.context import CliContext
from cli.cycle import add_cycle_arguments, run_cycle_command
from cli.external import register_group
from cli.monitoring import add_monitoring_arguments, run_monitoring_command
from cli.person_resolution import add_person_resolution_arguments, run_person_resolution_command


def register_person_resolution(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    register_group(subparsers, add_person_resolution_arguments, _person_resolution)


def register_monitoring(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    register_group(subparsers, add_monitoring_arguments, _monitoring)
    register_group(subparsers, add_cycle_arguments, _cycle)


def _person_resolution(args: argparse.Namespace, context: CliContext) -> None:
    run_person_resolution_command(
        args, context.session_factory, entity_review=context.settings.entity_review
    )


def _monitoring(args: argparse.Namespace, context: CliContext) -> None:
    run_monitoring_command(args, context.session_factory)


def _cycle(args: argparse.Namespace, context: CliContext) -> None:
    run_cycle_command(args, context.session_factory)

"""Areas whose commands are defined and run by their own `cli` module: person-resolution
review, monitoring and the evaluations.

The evaluations with a disposable database (`evaluate-er`, `evaluate-final`,
the real-world commands) get handlers that never touch the context,
so they can never reach the database in DATABASE_URL.
"""

from __future__ import annotations

import argparse

from cli.context import CliContext
from cli.cycle import add_cycle_arguments, run_cycle_command
from cli.external import register_group
from cli.monitoring import add_monitoring_arguments, run_monitoring_command
from cli.person_resolution import add_person_resolution_arguments, run_person_resolution_command
from evaluation.final.cli import add_final_evaluation_arguments, run_final_evaluation_command
from evaluation.person_resolution.cli import add_er_evaluation_arguments, run_evaluate_er
from evaluation.real_world.cli import add_real_world_arguments, run_real_world_command


def register_person_resolution(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    register_group(subparsers, add_person_resolution_arguments, _person_resolution)
    register_group(subparsers, add_er_evaluation_arguments, _evaluate_er)


def register_monitoring(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    register_group(subparsers, add_monitoring_arguments, _monitoring)
    register_group(subparsers, add_cycle_arguments, _cycle)


def register_evaluation(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    register_group(subparsers, add_final_evaluation_arguments, _evaluate_final)
    register_group(subparsers, add_real_world_arguments, _real_world)


def _person_resolution(args: argparse.Namespace, context: CliContext) -> None:
    run_person_resolution_command(
        args, context.session_factory, entity_review=context.settings.entity_review
    )


def _monitoring(args: argparse.Namespace, context: CliContext) -> None:
    run_monitoring_command(args, context.session_factory)


def _cycle(args: argparse.Namespace, context: CliContext) -> None:
    run_cycle_command(args, context.session_factory)


def _evaluate_er(args: argparse.Namespace, context: CliContext) -> None:
    run_evaluate_er(args)


def _evaluate_final(args: argparse.Namespace, context: CliContext) -> None:
    run_final_evaluation_command(args)


def _real_world(args: argparse.Namespace, context: CliContext) -> None:
    run_real_world_command(args)

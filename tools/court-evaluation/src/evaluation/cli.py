"""The evaluation tooling's command line: `court-evaluation <command>` (or
`python -m evaluation`). These commands were `python src/main.py <command>` before the
workspace split; the production command line no longer carries them, so nothing in
production depends on the evaluation.

The evaluations with a disposable database (`evaluate-er`, `evaluate-final`, the
real-world commands) never read DATABASE_URL; `compare-entity-models` reads the working
database the way a production command does, through `cli.context.CliContext`.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from typing import Any

from cli.context import CliContext
from evaluation.final.cli import add_final_evaluation_arguments, run_final_evaluation_command
from evaluation.model_comparison.compare import run_comparison
from evaluation.person_resolution.cli import add_er_evaluation_arguments, run_evaluate_er
from evaluation.real_world.cli import add_real_world_arguments, run_real_world_command
from observability import configure_logging

Handler = Callable[[argparse.Namespace], object]


def add_model_comparison_arguments(subparsers: Any) -> None:
    compare = subparsers.add_parser(
        "compare-entity-models",
        help=(
            "Ask the configured model (ENTITY_NORMALIZE_MODEL) what the steps already "
            "answered, on a sample, and report the agreement; writes nothing"
        ),
    )
    compare.add_argument("--step", choices=("names", "roles", "politics"), required=True)
    compare.add_argument("--sample", type=int, default=200)
    compare.add_argument("--seed", type=int, default=1)


def run_model_comparison_command(args: argparse.Namespace) -> None:
    configure_logging()
    report = run_comparison(
        CliContext().session_factory, args.step, size=args.sample, seed=args.seed
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


# In the order the commands had in `python src/main.py --help`.
_GROUPS: tuple[tuple[Callable[[Any], None], Handler], ...] = (
    (add_er_evaluation_arguments, run_evaluate_er),
    (add_final_evaluation_arguments, run_final_evaluation_command),
    (add_real_world_arguments, run_real_world_command),
    (add_model_comparison_arguments, run_model_comparison_command),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Court Monitor evaluations (tooling)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for add_arguments, handler in _GROUPS:
        before = set(subparsers.choices)
        add_arguments(subparsers)
        for name in sorted(set(subparsers.choices) - before):
            subparsers.choices[name].set_defaults(handler=handler)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.handler(args)

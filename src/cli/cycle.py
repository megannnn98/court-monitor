"""The steps of the operator cycle as commands: parse, call `OperatorCycle`, print.

JSON goes to stdout, structured logs to stderr. `orchestrator.operations` starts these
commands as subprocesses and reads both.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from analysis.service import RfCheckResult
from application import build_operator_cycle
from observability import configure_logging
from orchestrator.cycle import OperatorCycle

CYCLE_COMMANDS = frozenset(
    {
        "purge-junk",
        "collect-entities",
        "check-entities-rosfin",
        "find-figurants",
        "find-political",
        "find-unnamed",
        "find-news",
        "find-sentences",
        "compare-entity-models",
    }
)

CycleBuilder = Callable[[sessionmaker[Session]], OperatorCycle]


def add_cycle_arguments(subparsers: Any) -> None:
    subparsers.add_parser(
        "purge-junk",
        help=(
            "Delete the articles without a criminal-case event (keeping a tombstone of each "
            "post) and the persons only they named; irreversible"
        ),
    )

    subparsers.add_parser(
        "collect-entities",
        help="Rebuild the person entities from the articles with a criminal case",
    )

    subparsers.add_parser(
        "check-entities-rosfin",
        help=(
            "Download the Rosfinmonitoring list (a new snapshot when it changed) and match "
            "the person entities against the latest one by name"
        ),
    )

    subparsers.add_parser(
        "find-figurants",
        help=(
            "Tell the entities a criminal case is opened against from those only mentioned "
            "(rules, then a model for the rest)"
        ),
    )

    subparsers.add_parser(
        "find-political",
        help=(
            "Match entities against Rosfinmonitoring, tell which figurants are politically "
            "persecuted, then find the unnamed figurants"
        ),
    )

    subparsers.add_parser(
        "find-news",
        help=(
            "Tell what each political case's latest news is: a new case, a sentence, or "
            "more of an old one"
        ),
    )

    subparsers.add_parser(
        "find-sentences",
        help=(
            "Read the sentences the articles tell of: the region, the punishment and the "
            "reason of each, for the counts of «Спросить»"
        ),
    )

    subparsers.add_parser(
        "find-unnamed",
        help=(
            "Find the figurants the publications do not name («17-летний житель Тюмени») "
            "for matching with the Rosfinmonitoring list"
        ),
    )

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


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _rf_totals(checked: RfCheckResult | None) -> dict[str, object]:
    """The list check's figures; only the snapshot's absence when the check failed."""
    if checked is None:
        return {"snapshot_id": None}
    return {
        "snapshot_id": checked.snapshot_id,
        "snapshot_date": checked.snapshot_date.isoformat() if checked.snapshot_date else None,
        "entries": checked.entries,
        "new_snapshot": checked.new_snapshot,
        "download_error": checked.download_error,
        "entities": checked.entities,
        "rf_full": checked.full,
        "rf_possible": checked.possible,
        "rf_merged": checked.merged,
        "region_merged": checked.merged_region,
    }


def run_cycle_command(
    args: argparse.Namespace,
    session_factory: sessionmaker[Session],
    *,
    build_cycle: CycleBuilder = build_operator_cycle,
) -> bool:
    """Handle a command of the operator cycle; False when `args.command` is not one."""
    if args.command not in CYCLE_COMMANDS:
        return False
    configure_logging()
    if args.command == "compare-entity-models":
        # Tooling, not a step: it compares models on a sample and writes nothing.
        from evaluation.model_comparison.compare import run_comparison

        _print(run_comparison(session_factory, args.step, size=args.sample, seed=args.seed))
        return True
    cycle = build_cycle(session_factory)
    if args.command == "purge-junk":
        purged = cycle.purge()
        _print(
            {
                "articles": purged.purge.articles,
                "outdated": purged.purge.outdated,
                "held": purged.purge.held,
                "persons": purged.purge.persons,
                "reviews": purged.purge.reviews,
                "holds_released": purged.holds.released,
                "holds_model_junk": purged.holds.model_junk,
                "holds_failures": purged.holds.failures,
                "holds_unasked": purged.holds.unasked,
                "holds_restored": purged.holds.restored,
                "holds_cost_usd": purged.holds.cost_usd,
                "screen_cost_usd": purged.screen_cost_usd,
            }
        )
        return True
    if args.command == "collect-entities":
        collected = cycle.entities()
        _print(
            {
                "mentions": collected.mentions,
                "entities": collected.entities,
                "grouped": collected.grouped,
                "normalized_now": collected.normalized_now,
                "normalized_cached": collected.normalized_cached,
                "normalize_failures": collected.normalize_failures,
                "normalize_unasked": collected.normalize_unasked,
                "model_cost_usd": collected.model_cost_usd,
                "charges": collected.charges,
                "charged_entities": collected.charged_entities,
            }
        )
        return True
    if args.command == "check-entities-rosfin":
        checked = cycle.check_rosfinmonitoring()
        _print(_rf_totals(checked))
        return checked.snapshot_id is not None
    if args.command == "find-figurants":
        _print(dataclasses.asdict(cycle.figurants()))
        return True
    if args.command == "find-political":
        step = cycle.political()
        _print(
            {
                **_rf_totals(step.rf),
                "rf_error": step.rf_error,
                **dataclasses.asdict(step.politics),
                "digest_asked_now": step.digests.asked_now,
                "digest_cached": step.digests.cached,
                "digest_cost_usd": step.digests.cost_usd,
                "news_new_case": step.news.new_case,
                "news_sentence": step.news.sentence,
                "news_ongoing": step.news.ongoing,
                "news_closed": step.news.closed,
                "news_other": step.news.other,
                "news_unknown": step.news.unknown,
                "news_cost_usd": step.news.cost_usd,
                "unnamed": step.unnamed.unnamed,
                "unnamed_asked_now": step.unnamed.asked_now,
                "unnamed_failures": step.unnamed.failures,
                "unnamed_cost_usd": step.unnamed.cost_usd,
                "sentences": step.sentences.sentences,
                "sentences_asked_now": step.sentences.asked_now,
                "sentences_failures": step.sentences.failures,
                "sentences_unasked": step.sentences.unasked,
                "sentences_cost_usd": step.sentences.cost_usd,
            }
        )
        return True
    if args.command == "find-news":
        _print(dataclasses.asdict(cycle.news()))
        return True
    if args.command == "find-sentences":
        _print(dataclasses.asdict(cycle.sentences()))
        return True
    _print(dataclasses.asdict(cycle.unnamed()))
    return True

"""Manual monitoring without Dagster: the same `MonitoringService` the assets call.

JSON goes to stdout, structured `monitoring_*` logs to stderr.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from collections.abc import Callable
from datetime import date
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from application import ApplicationServices, build_application_services
from entities.collector import EntityCollector
from entities.news import NewsFinder, NewsResult, news_reader_from_env
from entities.normalizer import name_normalizer_from_env
from entities.politics import PoliticsFinder, politics_classifier_from_env
from entities.rf_check import EntityRfCheck, RfCheckResult
from entities.roles import FigurantFinder, role_classifier_from_env
from entities.unnamed import UnnamedFinder, UnnamedResult, unnamed_reader_from_env
from monitoring.junk_purge import JunkPurge, JunkPurgeResult, since_from_env
from monitoring.models import MonitoringAlreadyRunningError, MonitoringRunStatus, MonitoringTrigger
from monitoring.service import MonitoringService
from observability import configure_logging
from sources.source_registry import SOURCES, SourceKind

logger = logging.getLogger("monitoring")

MONITOR_EXIT_FAILED = 1
MONITOR_EXIT_ALREADY_RUNNING = 3

MONITORING_COMMANDS = frozenset(
    {
        "monitor",
        "monitor-derived",
        "monitor-resolve",
        "purge-junk",
        "collect-entities",
        "check-entities-rosfin",
        "find-figurants",
        "find-political",
        "find-unnamed",
        "find-news",
        "compare-entity-models",
        "monitoring-status",
        "monitoring-findings",
    }
)

ServicesBuilder = Callable[[sessionmaker[Session]], ApplicationServices]


def _default_builder(session_factory: sessionmaker[Session]) -> ApplicationServices:
    return build_application_services(session_factory=session_factory)


def add_monitoring_arguments(subparsers: Any) -> None:
    monitor = subparsers.add_parser(
        "monitor",
        help="Run automated monitoring once (all enabled sources or one source)",
    )
    monitor.add_argument(
        "--source",
        default=None,
        help="Monitor one source (default: MONITORING_ENABLED_SOURCES)",
    )
    monitor.add_argument(
        "--selected-source",
        action="append",
        default=None,
        help="Explicit news source selection for --catch-up; may be repeated",
    )
    monitor.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Discovery limit (default: MONITORING_DISCOVERY_LIMIT)",
    )
    monitor.add_argument(
        "--dry-run",
        action="store_true",
        help="Only discover and report new references; writes nothing",
    )
    monitor.add_argument(
        "--backfill",
        action="store_true",
        help="Manual backfill of one source with an explicit --limit; checkpoint unchanged",
    )
    monitor.add_argument(
        "--refetch-known",
        action="store_true",
        help="With --backfill: fetch already ingested documents again",
    )
    monitor.add_argument(
        "--catch-up",
        action="store_true",
        help="New publications of every source first, then the derived stages once",
    )
    monitor.add_argument(
        "--load-only",
        action="store_true",
        help=(
            "With --catch-up: load and extract only; person resolution and the derived "
            "stages are left to monitor-resolve"
        ),
    )
    monitor.add_argument(
        "--published-from",
        type=date.fromisoformat,
        default=None,
        help="Only ingest articles published on or after YYYY-MM-DD",
    )
    monitor.add_argument(
        "--published-to",
        type=date.fromisoformat,
        default=None,
        help="Only ingest articles published on or before YYYY-MM-DD",
    )

    resolve = subparsers.add_parser(
        "monitor-resolve",
        help=(
            "Resolve every pending person mention of the news sources, then run the "
            "derived stages once (no web access)"
        ),
    )
    resolve.add_argument(
        "--selected-source",
        action="append",
        default=None,
        help="News source to resolve (default: every news source); may be repeated",
    )

    subparsers.add_parser(
        "monitor-derived",
        help="Re-run classification, RF matching, semantic indexing and findings (no web access)",
    )

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

    status = subparsers.add_parser("monitoring-status", help="Show monitoring runs and checkpoints")
    status.add_argument("--run-id", type=int, default=None, help="Show one run with failed items")

    findings = subparsers.add_parser("monitoring-findings", help="List monitoring findings")
    findings.add_argument("--all", action="store_true", help="Include inactive findings")
    findings.add_argument("--limit", type=int, default=100)


def _print(payload: BaseModel | list[Any] | dict[str, Any]) -> None:
    if isinstance(payload, BaseModel):
        print(payload.model_dump_json(indent=2))
        return
    print(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
            default=lambda value: value.model_dump(mode="json"),
        )
    )


def _compare_models(
    session_factory: sessionmaker[Session], step: str, sample: int, seed: int
) -> bool:
    from entities import compare

    if step == "names":
        normalizer = name_normalizer_from_env()
        if normalizer is None:
            raise SystemExit("No model configured: OPENROUTER_API_KEY is not set")
        result = compare.compare_names(session_factory, normalizer, size=sample, seed=seed)
    elif step == "roles":
        roles = role_classifier_from_env()
        if roles is None:
            raise SystemExit("No model configured: OPENROUTER_API_KEY is not set")
        result = compare.compare_roles(session_factory, roles, size=sample, seed=seed)
    else:
        politics = politics_classifier_from_env()
        if politics is None:
            raise SystemExit("No model configured: OPENROUTER_API_KEY is not set")
        result = compare.compare_politics(session_factory, politics, size=sample, seed=seed)
    _print(result.report())
    return True


def run_monitoring_command(
    args: argparse.Namespace,
    session_factory: sessionmaker[Session],
    *,
    build_services: ServicesBuilder = _default_builder,
) -> bool:
    """Handle a monitoring command; False when `args.command` is not one."""
    if args.command not in MONITORING_COMMANDS:
        return False
    configure_logging()
    if args.command == "purge-junk":
        return _purge_junk(session_factory)
    if args.command == "collect-entities":
        normalizer = name_normalizer_from_env()
        if normalizer is None:
            logger.warning(
                "event=entity_names_no_model: neither OPENROUTER_API_KEY nor ANTHROPIC_API_KEY"
            )
        collected = EntityCollector(
            session_factory,
            on_stage=lambda stage: logger.info("event=entities_collect_stage stage=%s", stage),
            normalizer=normalizer,
        ).run()
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
        checked = EntityRfCheck(
            session_factory,
            on_stage=lambda stage: logger.info("event=entities_rf_check_stage stage=%s", stage),
        ).run()
        _print(
            {
                "snapshot_id": checked.snapshot_id,
                "snapshot_date": checked.snapshot_date.isoformat()
                if checked.snapshot_date
                else None,
                "entries": checked.entries,
                "new_snapshot": checked.new_snapshot,
                "download_error": checked.download_error,
                "entities": checked.entities,
                "rf_full": checked.full,
                "rf_possible": checked.possible,
                "rf_merged": checked.merged,
                "region_merged": checked.merged_region,
            }
        )
        return checked.snapshot_id is not None
    if args.command == "find-figurants":
        classifier = role_classifier_from_env()
        if classifier is None:
            logger.warning("event=entity_roles_no_model: OPENROUTER_API_KEY is not set")
        found = FigurantFinder(
            session_factory,
            classifier=classifier,
            on_stage=lambda stage: logger.info("event=entity_figurants_stage stage=%s", stage),
        ).run()
        _print(dataclasses.asdict(found))
        return True
    if args.command == "find-political":
        # The list first: it confirms who is who before the verdicts. Its failure (the
        # site, the database) must not cost the verdicts: they run, and the output says why
        # the list was not checked.
        rf_error: str | None = None
        rf_checked: RfCheckResult | None = None
        try:
            rf_checked = EntityRfCheck(
                session_factory,
                on_stage=lambda stage: logger.info("event=entities_rf_check_stage stage=%s", stage),
            ).run()
        except Exception as exc:  # reported in the output; the step goes on
            logger.exception("event=entities_rf_check_failed")
            rf_error = f"{type(exc).__name__}: {exc}"
        politics = politics_classifier_from_env()
        if politics is None:
            logger.warning("event=entity_politics_no_model: OPENROUTER_API_KEY is not set")
        found_political = PoliticsFinder(
            session_factory,
            classifier=politics,
            on_stage=lambda stage: logger.info("event=entity_politics_stage stage=%s", stage),
        ).run()
        # What each political case's latest news is: the new cases and the sentences first.
        news = _find_news(session_factory)
        # The final step ends with unnamed figurants: their cases are read the same way.
        unnamed = _find_unnamed(session_factory)
        _print(
            {
                **_rf_totals(rf_checked),
                "rf_error": rf_error,
                **dataclasses.asdict(found_political),
                "news_new_case": news.new_case,
                "news_sentence": news.sentence,
                "news_ongoing": news.ongoing,
                "news_closed": news.closed,
                "news_other": news.other,
                "news_unknown": news.unknown,
                "news_cost_usd": news.cost_usd,
                "unnamed": unnamed.unnamed,
                "unnamed_asked_now": unnamed.asked_now,
                "unnamed_failures": unnamed.failures,
                "unnamed_cost_usd": unnamed.cost_usd,
            }
        )
        return True
    if args.command == "find-news":
        _print(dataclasses.asdict(_find_news(session_factory)))
        return True
    if args.command == "find-unnamed":
        _print(dataclasses.asdict(_find_unnamed(session_factory)))
        return True
    if args.command == "compare-entity-models":
        return _compare_models(session_factory, args.step, args.sample, args.seed)
    services = build_services(session_factory)
    monitoring = services.monitoring

    if args.command == "monitoring-status":
        if args.run_id is None:
            _print(monitoring.status())
            return True
        details = services.monitoring_repository.get_run_details(args.run_id)
        if details is None:
            raise SystemExit(f"Monitoring run not found: {args.run_id}")
        _print(details)
        return True

    if args.command == "monitoring-findings":
        _print(services.findings.list_findings(active_only=not args.all, limit=args.limit))
        return True

    if args.command == "monitor-derived":
        try:
            run = monitoring.run_derived()
        except MonitoringAlreadyRunningError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(MONITOR_EXIT_ALREADY_RUNNING) from None
        _print(run)
        if run.status is MonitoringRunStatus.FAILED:
            raise SystemExit(MONITOR_EXIT_FAILED)
        return True

    if args.command == "monitor-resolve":
        return _resolve(args, monitoring)

    if args.load_only and not args.catch_up:
        raise SystemExit("--load-only requires --catch-up")
    if args.catch_up and (args.dry_run or args.backfill):
        raise SystemExit("--catch-up cannot be combined with --dry-run or --backfill")
    if (
        args.published_from is not None
        and args.published_to is not None
        and args.published_from > args.published_to
    ):
        raise SystemExit("--published-from must be <= --published-to")
    if (args.published_from is not None or args.published_to is not None) and args.dry_run:
        raise SystemExit(
            "published date filters require fetching/parsing; --dry-run cannot apply them"
        )
    if args.selected_source is not None:
        if not args.catch_up:
            raise SystemExit("--selected-source requires --catch-up")
        if args.source is not None:
            raise SystemExit("--selected-source cannot be combined with --source")
        _require_news_sources(args.selected_source)
    if args.refetch_known and not args.backfill:
        raise SystemExit("--refetch-known requires --backfill")
    if args.backfill and (args.source is None or args.limit is None):
        raise SystemExit("--backfill requires an explicit --source and --limit")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be greater than 0")
    sources = (
        list(dict.fromkeys(args.selected_source))
        if args.selected_source is not None
        else [args.source]
        if args.source
        else list(monitoring.settings.enabled_sources)
    )

    if args.dry_run:
        try:
            _print([monitoring.dry_run(source, discovery_limit=args.limit) for source in sources])
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        return True

    trigger = MonitoringTrigger.BACKFILL if args.backfill else MonitoringTrigger.MANUAL
    results: list[dict[str, Any]] = []
    exit_code = 0
    for source in sources:
        try:
            run = monitoring.run_source(
                source,
                trigger=trigger,
                discovery_limit=args.limit,
                refetch_known=args.refetch_known,
                published_from=args.published_from,
                published_to=args.published_to,
                with_derived=not args.catch_up,
                with_resolution=not args.load_only,
            )
        except MonitoringAlreadyRunningError as exc:
            results.append(
                {
                    "source": source,
                    "skipped": "already_running",
                    "running_run_id": exc.running_run_id,
                }
            )
            exit_code = exit_code or MONITOR_EXIT_ALREADY_RUNNING
            continue
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        results.append(run.model_dump(mode="json"))
        if run.status is MonitoringRunStatus.FAILED:
            exit_code = MONITOR_EXIT_FAILED
    if args.catch_up and not args.load_only:
        try:
            derived = monitoring.run_derived(trigger=MonitoringTrigger.MANUAL)
        except MonitoringAlreadyRunningError as exc:
            results.append({"skipped": "already_running", "running_run_id": exc.running_run_id})
            exit_code = exit_code or MONITOR_EXIT_ALREADY_RUNNING
        else:
            results.append(derived.model_dump(mode="json"))
            if derived.status is MonitoringRunStatus.FAILED:
                exit_code = MONITOR_EXIT_FAILED
    _print(results)
    if exit_code:
        raise SystemExit(exit_code)
    return True


def _require_news_sources(sources: list[str]) -> None:
    for source in sources:
        definition = SOURCES.get(source)
        if definition is None:
            raise SystemExit(f"Unknown source: {source}")
        if definition.kind is not SourceKind.NEWS:
            raise SystemExit(f"Source is not a news source: {source}")


def _resolve(args: argparse.Namespace, monitoring: MonitoringService) -> bool:
    """Resolution of every selected source, one after another, then the derived stages.

    One source at a time: resolution decides against the persons created so far, and
    parallel workers could create the same person twice."""
    if args.selected_source is not None:
        _require_news_sources(args.selected_source)
        sources = list(dict.fromkeys(args.selected_source))
    else:
        sources = [name for name, item in SOURCES.items() if item.kind is SourceKind.NEWS]
    results: list[dict[str, Any]] = []
    exit_code = 0
    for source in sources:
        try:
            run = monitoring.resolve_source(source, trigger=MonitoringTrigger.MANUAL)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        except MonitoringAlreadyRunningError as exc:
            results.append(
                {
                    "source": source,
                    "skipped": "already_running",
                    "running_run_id": exc.running_run_id,
                }
            )
            exit_code = exit_code or MONITOR_EXIT_ALREADY_RUNNING
            continue
        results.append(run.model_dump(mode="json"))
        if run.status is MonitoringRunStatus.FAILED:
            exit_code = MONITOR_EXIT_FAILED
    try:
        derived = monitoring.run_derived(trigger=MonitoringTrigger.MANUAL)
    except MonitoringAlreadyRunningError as exc:
        results.append({"skipped": "already_running", "running_run_id": exc.running_run_id})
        exit_code = exit_code or MONITOR_EXIT_ALREADY_RUNNING
    else:
        results.append(derived.model_dump(mode="json"))
        if derived.status is MonitoringRunStatus.FAILED:
            exit_code = MONITOR_EXIT_FAILED
    _print(results)
    if exit_code:
        raise SystemExit(exit_code)
    return True


def _rf_totals(checked: RfCheckResult | None) -> dict[str, object]:
    """The list check's figures in the final step's output; none when it failed."""
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


def _find_news(session_factory: sessionmaker[Session]) -> NewsResult:
    reader = news_reader_from_env()
    if reader is None:
        logger.warning("event=entity_news_no_model: OPENROUTER_API_KEY is not set")
    return NewsFinder(
        session_factory,
        reader=reader,
        on_stage=lambda stage: logger.info("event=entity_news_stage stage=%s", stage),
    ).run()


def _find_unnamed(session_factory: sessionmaker[Session]) -> UnnamedResult:
    reader = unnamed_reader_from_env()
    if reader is None:
        logger.warning("event=unnamed_no_model: OPENROUTER_API_KEY is not set")
    return UnnamedFinder(
        session_factory,
        reader=reader,
        on_stage=lambda stage: logger.info("event=unnamed_stage stage=%s", stage),
    ).run()


def _purge_junk(session_factory: sessionmaker[Session]) -> bool:
    """Progress goes to stderr as `event=junk_purge_progress`, the totals to stdout."""

    def progress(result: JunkPurgeResult) -> None:
        logger.info(
            "event=junk_purge_progress articles=%d total=%d persons=%d reviews=%d outdated=%d",
            result.articles,
            total,
            result.persons,
            result.reviews,
            result.outdated,
        )

    since = since_from_env()
    purge = JunkPurge(session_factory, on_progress=progress, since=since)
    total = purge.count()
    logger.info("event=junk_purge_started total=%d since=%s", total, since.date() if since else "-")
    result = purge.run()
    _print(
        {
            "articles": result.articles,
            "outdated": result.outdated,
            "persons": result.persons,
            "reviews": result.reviews,
        }
    )
    return True

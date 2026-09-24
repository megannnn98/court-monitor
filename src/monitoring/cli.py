"""Manual monitoring without Dagster: the same `MonitoringService` the assets call.

JSON goes to stdout, structured `monitoring_*` logs to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from application import ApplicationServices, build_application_services
from monitoring.models import MonitoringAlreadyRunningError, MonitoringRunStatus, MonitoringTrigger
from monitoring.service import MonitoringService
from observability import configure_logging
from sources.source_registry import SOURCES, SourceKind

MONITOR_EXIT_FAILED = 1
MONITOR_EXIT_ALREADY_RUNNING = 3

MONITORING_COMMANDS = frozenset(
    {"monitor", "monitor-derived", "monitor-resolve", "monitoring-status", "monitoring-findings"}
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

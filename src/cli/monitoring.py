"""Manual monitoring without Dagster: the same `MonitoringService` the assets call.

Parse, call the orchestrator, print. JSON goes to stdout, structured `monitoring_*`
logs to stderr. The steps of the operator cycle are `cli.cycle`.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from application import ApplicationServices, build_application_services
from ingestion.concurrency import FetchLimits
from ingestion.sources.source_registry import SOURCES, SourceKind
from observability import configure_logging
from orchestrator.models import (
    MonitoringAlreadyRunningError,
    MonitoringRunStatus,
    MonitoringTrigger,
)
from orchestrator.service import MonitoringService

logger = logging.getLogger("monitoring")

MONITOR_EXIT_FAILED = 1
MONITOR_EXIT_ALREADY_RUNNING = 3

MONITORING_COMMANDS = frozenset(
    {
        "monitor",
        "monitor-derived",
        "monitor-resolve",
        "monitoring-status",
        "monitoring-findings",
    }
)

ServicesBuilder = Callable[[sessionmaker[Session]], ApplicationServices]

MAIN_SCRIPT = Path(__file__).resolve().parents[1] / "main.py"
# How long interrupted lanes get, together, to close their runs before they are
# killed: less than the console's own STOP_GRACE (30 s) for the parent, so the
# parent is still there to kill a lane that stays.
LANE_STOP_GRACE_SECONDS = 20.0
_PR_SET_PDEATHSIG = 1
# Set by the parent of a parallel load: the pid a lane must outlive no longer than.
LANE_PARENT_ENV = "COURT_MONITOR_LANE_PARENT"


def interrupt_when_parent_dies(parent: int) -> bool:
    """SIGINT for this process when `parent` dies, even of SIGKILL (Linux only);
    False when it is already gone.

    A lane runs in a session of its own, so nothing else would stop it once its
    parent is gone; SIGINT lets it close its runs as any interrupted CLI run does."""
    if sys.platform.startswith("linux"):
        ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_PDEATHSIG, signal.SIGINT)
    return os.getppid() == parent


class LaneHandle(Protocol):
    """One lane of a parallel load, started and still running."""

    def wait(self) -> tuple[int, list[Any]]:
        """Exit code and the JSON results of the lane."""
        ...

    def signal_stop(self) -> None:
        """Ask the lane to stop; returns at once."""
        ...

    def finish_stop(self, deadline: float) -> None:
        """Wait for the lane until `deadline` (time.monotonic()), then kill it."""
        ...


LaneStarter = Callable[[list[str], Mapping[str, str]], LaneHandle]


@dataclass(frozen=True)
class Lane:
    """Sources loaded one after another in a child of their own."""

    host: str
    sources: list[str]
    # One of several lanes of the same host: each may send it one request at a time,
    # so together they never exceed the host's limit.
    shared: bool

    def env(self, base: Mapping[str, str]) -> dict[str, str]:
        """What the child's environment changes from `base`."""
        if not self.shared:
            return {}
        current = (base.get("INGESTION_DOMAIN_CONCURRENCY") or "").strip().strip(",")
        # A later entry for a host wins over an earlier one.
        entry = f"{self.host}=1"
        return {"INGESTION_DOMAIN_CONCURRENCY": f"{current},{entry}" if current else entry}


class _LaneProcess:
    """A lane as a child `python src/main.py monitor ... --lane`.

    A process, not a thread: SIGINT closes a child's monitoring runs the way it does
    for any CLI run. Its own session, so a terminal Ctrl+C reaches only the parent,
    which passes it on once."""

    def __init__(
        self,
        argv: list[str],
        env: Mapping[str, str],
        *,
        command: Sequence[str] = (sys.executable, str(MAIN_SCRIPT)),
    ) -> None:
        self._process = subprocess.Popen(
            [*command, *argv],
            env=os.environ | dict(env) | {LANE_PARENT_ENV: str(os.getpid())},
            stdout=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

    def wait(self) -> tuple[int, list[Any]]:
        output, _ = self._process.communicate()
        try:
            results = json.loads(output) if output.strip() else []
        except json.JSONDecodeError:
            results = [{"lane_output_unreadable": output[-2000:]}]
        return self._process.returncode, results if isinstance(results, list) else [results]

    def signal_stop(self) -> None:
        if self._process.poll() is None:
            self._process.send_signal(signal.SIGINT)

    def finish_stop(self, deadline: float) -> None:
        try:
            self._process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()


def _stop_lanes(handles: Sequence[LaneHandle]) -> None:
    """Every lane gets SIGINT at once, then one shared grace, then SIGKILL: a lane
    that stays must not hold back the others' signal."""
    for handle in handles:
        handle.signal_stop()
    deadline = time.monotonic() + LANE_STOP_GRACE_SECONDS
    for handle in handles:
        handle.finish_stop(deadline)


def source_lanes(sources: Sequence[str], limits: FetchLimits) -> list[Lane]:
    """Sources grouped by the host they download from, in their given order.

    Every site loads in a lane of its own. A host of many sources (every Telegram
    channel is on t.me) gets as many lanes as it allows requests at once, each sending
    one at a time: the waits of one channel's discovery overlap another's work, and
    the host never sees more requests than its limit."""
    by_host: dict[str, list[str]] = {}
    for source in sources:
        host = urlsplit(SOURCES[source].base_url).hostname or source
        by_host.setdefault(host, []).append(source)
    lanes: list[Lane] = []
    for host, host_sources in by_host.items():
        count = min(len(host_sources), limits.concurrency_for(host))
        lanes += [
            Lane(host=host, sources=host_sources[index::count], shared=count > 1)
            for index in range(count)
        ]
    return lanes


def lane_arguments(args: argparse.Namespace, sources: Sequence[str]) -> list[str]:
    """The command line of a lane: the same load, for its sources only."""
    argv = ["monitor", "--catch-up", "--load-only", "--lane"]
    for source in sources:
        argv += ["--selected-source", source]
    if args.limit is not None:
        argv += ["--limit", str(args.limit)]
    if args.published_from is not None:
        argv += ["--published-from", args.published_from.isoformat()]
    if args.published_to is not None:
        argv += ["--published-to", args.published_to.isoformat()]
    return argv


def merge_lane_exit_codes(codes: Sequence[int]) -> int:
    """As the sequential load: a failure outweighs a source that was already running."""
    if MONITOR_EXIT_FAILED in codes:
        return MONITOR_EXIT_FAILED
    if MONITOR_EXIT_ALREADY_RUNNING in codes:
        return MONITOR_EXIT_ALREADY_RUNNING
    return next((code for code in codes if code), 0)


def _load_in_lanes(
    args: argparse.Namespace, sources: Sequence[str], lanes: list[Lane], start_lane: LaneStarter
) -> None:
    logger.info(
        "event=monitoring_parallel_load lanes=%d sources=%d hosts=%s",
        len(lanes),
        sum(len(lane.sources) for lane in lanes),
        ",".join(f"{lane.host}:{len(lane.sources)}" for lane in lanes),
    )
    handles: list[LaneHandle] = []
    try:
        for lane in lanes:
            handles.append(start_lane(lane_arguments(args, lane.sources), lane.env(os.environ)))
        finished = [handle.wait() for handle in handles]
    except BaseException:
        _stop_lanes(handles)
        raise
    # As a sequential load would print them: in the order the sources were given.
    position = {source: index for index, source in enumerate(sources)}

    def order(result: Any) -> int:
        source = result.get("source") if isinstance(result, dict) else None
        return position.get(source, len(position)) if isinstance(source, str) else len(position)

    results = sorted((result for _, lane_results in finished for result in lane_results), key=order)
    _print(results)
    exit_code = merge_lane_exit_codes([code for code, _ in finished])
    if exit_code:
        raise SystemExit(exit_code)


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
    # Set on the children of a parallel load: load these sources here, in one process.
    monitor.add_argument("--lane", action="store_true", help=argparse.SUPPRESS)

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
        help="Re-run classification, RF matching and findings (no web access)",
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
    start_lane: LaneStarter = _LaneProcess,
) -> bool:
    """Handle a monitoring command; False when `args.command` is not one."""
    if args.command not in MONITORING_COMMANDS:
        return False
    parent = os.environ.get(LANE_PARENT_ENV)
    if getattr(args, "lane", False) and parent and not interrupt_when_parent_dies(int(parent)):
        # The parallel load that started this lane is gone: load nothing.
        raise SystemExit(MONITOR_EXIT_FAILED)
    configure_logging()
    services = build_services(session_factory)
    monitoring = services.monitoring

    if args.command == "monitoring-status":
        if args.run_id is None:
            _print(monitoring.status())
            return True
        details = services.runs.get_run_details(args.run_id)
        if details is None:
            raise SystemExit(f"Monitoring run not found: {args.run_id}")
        _print(details)
        return True

    if args.command == "monitoring-findings":
        _print(services.results.list_findings(active_only=not args.all, limit=args.limit))
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

    # A load downloads every site at once: one child per host. Resolution stays
    # sequential (it could create a person twice), so only a load-only catch-up splits.
    if args.catch_up and args.load_only and not args.lane:
        lanes = source_lanes(sources, FetchLimits.from_env())
        if len(lanes) > 1:
            _load_in_lanes(args, sources, lanes, start_lane)
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

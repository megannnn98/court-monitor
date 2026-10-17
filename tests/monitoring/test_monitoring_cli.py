"""`monitor`, `monitor-derived`, `monitor-resolve`, `monitoring-status`,
`monitoring-findings` CLI commands."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import (
    MEDIA_ONLY,
    PETROV,
    SIDOROV,
    FakeUpstream,
    build_service,
    table_counts,
)
from support.workspace import APP_SRC

from application import ApplicationServices
from cli.cycle import add_cycle_arguments, run_cycle_command
from cli.monitoring import (
    MONITOR_EXIT_ALREADY_RUNNING,
    MONITOR_EXIT_FAILED,
    Lane,
    add_monitoring_arguments,
    lane_arguments,
    merge_lane_exit_codes,
    run_monitoring_command,
    source_lanes,
)
from ingestion.concurrency import FetchLimits
from orchestrator.models import MonitoringStage, MonitoringTrigger
from orchestrator.repository import SqlAlchemyMonitoringRepository
from orchestrator.runs import MonitoringRuns
from results.findings import MonitoringFindingService
from results.service import ResultsService


def _parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_monitoring_arguments(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(argv)


def _run(
    session_factory: sessionmaker[Session],
    upstreams: dict[str, FakeUpstream],
    capsys: pytest.CaptureFixture[str],
    *argv: str,
    lanes: list[list[str]] | None = None,
) -> object:
    service = build_service(session_factory, upstreams)

    def build(factory: sessionmaker[Session]) -> ApplicationServices:
        return ApplicationServices(
            session_factory=factory,
            monitoring_settings=service.settings,
            monitoring=service,
            runs=MonitoringRuns(SqlAlchemyMonitoringRepository(factory)),
            results=ResultsService(
                session_factory=factory, findings=MonitoringFindingService(factory)
            ),
        )

    started_lanes = lanes if lanes is not None else []

    class InProcessLane:
        """A lane run in this process, in place of a child `python src/main.py`."""

        def __init__(self, lane_argv: list[str], env: Mapping[str, str]) -> None:
            started_lanes.append(lane_argv)
            output = io.StringIO()
            self._exit_code = 0
            with contextlib.redirect_stdout(output):
                try:
                    run_monitoring_command(
                        _parse(*lane_argv), session_factory, build_services=build
                    )
                except SystemExit as exc:
                    self._exit_code = int(exc.code or 0)
            self._results = json.loads(output.getvalue())

        def wait(self) -> tuple[int, list[Any]]:
            return self._exit_code, self._results

        def signal_stop(self) -> None:
            pass

        def finish_stop(self, deadline: float) -> None:
            pass

    assert run_monitoring_command(
        _parse(*argv), session_factory, build_services=build, start_lane=InProcessLane
    )
    return json.loads(capsys.readouterr().out)


def test_non_monitoring_command_is_not_handled(session_factory: sessionmaker[Session]) -> None:
    assert not run_monitoring_command(argparse.Namespace(command="search"), session_factory)


def test_monitor_all_enabled_sources_then_status(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    ovd = FakeUpstream()
    ovd.publish("sidorov", SIDOROV)
    sota = FakeUpstream()
    sota.publish("petrov", PETROV)
    upstreams = {"ovd-info": ovd, "sota-vision": sota}

    runs = _run(session_factory, upstreams, capsys, "monitor")
    status = _run(session_factory, upstreams, capsys, "monitoring-status")

    assert isinstance(runs, list)
    assert [(run["source"], run["status"], run["documents_ingested"]) for run in runs] == [
        ("ovd-info", "completed", 1),
        ("sota-vision", "completed", 1),
    ]
    assert isinstance(status, dict)
    assert {state["source_name"] for state in status["sources"]} == {"ovd-info", "sota-vision"}
    assert status["running"] == []


def test_monitor_one_source_dry_run_writes_nothing(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    ovd = FakeUpstream()
    ovd.publish("sidorov", SIDOROV)
    ovd.publish("petrov", PETROV)

    plans = _run(
        session_factory, {"ovd-info": ovd}, capsys, "monitor", "--source", "ovd-info", "--dry-run"
    )
    status = _run(session_factory, {"ovd-info": ovd}, capsys, "monitoring-status")

    assert isinstance(plans, list)
    assert [(plan["source"], plan["discovered"], plan["estimated_new"]) for plan in plans] == [
        ("ovd-info", 2, 2)
    ]
    assert ovd.fetches == []
    assert isinstance(status, dict)
    assert status["latest_runs"] == []
    assert status["sources"] == []


def test_monitor_date_range_persists_only_articles_in_range(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    ovd = FakeUpstream()
    ovd.publish("old", SIDOROV, published_at=datetime(2026, 8, 31, tzinfo=UTC))
    ovd.publish("new", PETROV, published_at=datetime(2026, 9, 5, tzinfo=UTC))

    runs = _run(
        session_factory,
        {"ovd-info": ovd},
        capsys,
        "monitor",
        "--catch-up",
        "--load-only",
        "--selected-source",
        "ovd-info",
        "--published-from",
        "2026-09-01",
        "--published-to",
        "2026-09-30",
    )

    assert isinstance(runs, list)
    (run,) = runs
    assert run["documents_ingested"] == 1
    assert run["documents_skipped"] == 1
    assert run["stage_metrics"]["ingestion"]["skipped_out_of_published_range"] == 1
    assert ovd.fetches == ["old", "new"]
    assert table_counts(session_factory, "source_documents", "parsed_articles") == {
        "source_documents": 1,
        "parsed_articles": 1,
    }


def test_monitor_reports_an_already_running_source(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    ovd = FakeUpstream()
    running = build_service(session_factory, {"ovd-info": ovd}).start_source_run("ovd-info")

    with pytest.raises(SystemExit) as raised:
        _run(session_factory, {"ovd-info": ovd}, capsys, "monitor", "--source", "ovd-info")

    assert raised.value.code == MONITOR_EXIT_ALREADY_RUNNING
    output = json.loads(capsys.readouterr().out)
    assert output == [
        {"source": "ovd-info", "skipped": "already_running", "running_run_id": running.run_id}
    ]


SOURCE_STAGES = {"discovery", "ingestion", "extraction", "resolution"}


def test_catch_up_runs_the_derived_stages_once_after_every_source(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    ovd = FakeUpstream()
    ovd.publish("sidorov", SIDOROV)
    sota = FakeUpstream()
    sota.publish("petrov", PETROV)

    runs = _run(
        session_factory, {"ovd-info": ovd, "sota-vision": sota}, capsys, "monitor", "--catch-up"
    )

    assert isinstance(runs, list)
    *sources, derived = runs
    assert [(run["source"], run["status"], run["documents_ingested"]) for run in sources] == [
        ("ovd-info", "completed", 1),
        ("sota-vision", "completed", 1),
    ]
    assert all(set(run["stage_metrics"]) == SOURCE_STAGES for run in sources)
    assert (derived["scope"], derived["status"]) == ("derived", "completed")
    assert "classification" in derived["stage_metrics"]
    assert derived["classifications_created"] >= 1  # the persons of both sources


def test_catch_up_goes_on_after_a_failed_source(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    broken = FakeUpstream()
    broken.discovery_error = RuntimeError("listing is down")
    sota = FakeUpstream()
    sota.publish("petrov", PETROV)

    with pytest.raises(SystemExit) as raised:
        _run(
            session_factory,
            {"ovd-info": broken, "sota-vision": sota},
            capsys,
            "monitor",
            "--catch-up",
        )

    assert raised.value.code == 1
    runs = json.loads(capsys.readouterr().out)
    assert [(run.get("source"), run["status"]) for run in runs] == [
        ("ovd-info", "failed"),
        ("sota-vision", "completed"),
        (None, "completed"),
    ]


def test_catch_up_runs_only_the_explicitly_selected_sources(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    ovd = FakeUpstream()
    ovd.publish("sidorov", SIDOROV)
    sota = FakeUpstream()
    sota.publish("petrov", PETROV)

    runs = _run(
        session_factory,
        {"ovd-info": ovd, "sota-vision": sota},
        capsys,
        "monitor",
        "--catch-up",
        "--selected-source",
        "sota-vision",
    )

    assert isinstance(runs, list)
    assert [(run["source"], run["status"]) for run in runs] == [
        ("sota-vision", "completed"),
        (None, "completed"),
    ]
    assert ovd.fetches == []
    assert len(sota.fetches) == 1


def test_selected_catch_up_continues_after_one_selected_source_fails(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    broken = FakeUpstream()
    broken.discovery_error = RuntimeError("listing is down")
    sota = FakeUpstream()
    sota.publish("petrov", PETROV)

    with pytest.raises(SystemExit) as raised:
        _run(
            session_factory,
            {"ovd-info": broken, "sota-vision": sota},
            capsys,
            "monitor",
            "--catch-up",
            "--selected-source",
            "ovd-info",
            "--selected-source",
            "sota-vision",
        )

    assert raised.value.code == 1
    runs = json.loads(capsys.readouterr().out)
    assert [(run.get("source"), run["status"]) for run in runs] == [
        ("ovd-info", "failed"),
        ("sota-vision", "completed"),
        (None, "completed"),
    ]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (("monitor", "--backfill", "--source", "ovd-info"), "--backfill requires"),
        (("monitor", "--refetch-known"), "--refetch-known requires --backfill"),
        (("monitor", "--source", "unknown"), "Unknown source: unknown"),
        (("monitor", "--selected-source", "ovd-info"), "requires --catch-up"),
        (
            ("monitor", "--catch-up", "--selected-source", "memopzk-figurants"),
            "not a news source",
        ),
        (("monitor", "--catch-up", "--dry-run"), "--catch-up cannot"),
        (("monitor", "--load-only"), "--load-only requires --catch-up"),
        (("monitor-resolve", "--selected-source", "memopzk-figurants"), "not a news source"),
        (
            ("monitor", "--catch-up", "--backfill", "--source", "ovd-info", "--limit", "5"),
            "--catch-up cannot",
        ),
        (
            (
                "monitor",
                "--published-from",
                "2026-09-30",
                "--published-to",
                "2026-09-01",
            ),
            "--published-from must be <= --published-to",
        ),
        (
            ("monitor", "--published-from", "2026-09-01", "--dry-run"),
            "published date filters require fetching/parsing",
        ),
    ],
)
def test_monitor_rejects_invalid_options(
    session_factory: sessionmaker[Session],
    capsys: pytest.CaptureFixture[str],
    argv: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(SystemExit) as raised:
        _run(session_factory, {"ovd-info": FakeUpstream()}, capsys, *argv)
    assert message in str(raised.value.code)


def test_derived_run_and_run_details(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    upstreams = {"ovd-info": FakeUpstream()}

    derived = _run(session_factory, upstreams, capsys, "monitor-derived")
    assert isinstance(derived, dict)
    details = _run(
        session_factory, upstreams, capsys, "monitoring-status", "--run-id", str(derived["id"])
    )
    findings = _run(session_factory, upstreams, capsys, "monitoring-findings", "--all")

    assert derived["trigger_type"] == "derived"
    assert derived["status"] == "completed"
    assert isinstance(details, dict)
    assert details["run"]["id"] == derived["id"]
    assert details["items"] == []
    assert findings == []


def test_a_load_only_catch_up_leaves_resolution_to_monitor_resolve(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    ovd = FakeUpstream()
    ovd.publish("sidorov", SIDOROV)
    sota = FakeUpstream()
    sota.publish("petrov", PETROV)
    upstreams = {"ovd-info": ovd, "sota-vision": sota}
    selection = ("--selected-source", "ovd-info", "--selected-source", "sota-vision")

    lanes: list[list[str]] = []
    loaded = _run(
        session_factory,
        upstreams,
        capsys,
        "monitor",
        "--catch-up",
        "--load-only",
        *selection,
        lanes=lanes,
    )
    resolved = _run(session_factory, upstreams, capsys, "monitor-resolve", *selection)

    # The load: one lane per site, each loading only its own source; two source runs,
    # no resolution, no derived run.
    assert [lane[-1] for lane in lanes] == ["ovd-info", "sota-vision"]
    assert all("--lane" in lane for lane in lanes)
    assert isinstance(loaded, list)
    assert [(run["source"], run["status"], run["documents_ingested"]) for run in loaded] == [
        ("ovd-info", "completed", 1),
        ("sota-vision", "completed", 1),
    ]
    assert all(set(run["stage_metrics"]) == SOURCE_STAGES - {"resolution"} for run in loaded)
    assert all(run["persons_created"] == 0 for run in loaded)
    # The resolution: only that stage per source, fetching nothing, then the derived run.
    assert isinstance(resolved, list)
    *sources, derived = resolved
    assert [(run["source"], run["status"]) for run in sources] == [
        ("ovd-info", "completed"),
        ("sota-vision", "completed"),
    ]
    assert all(set(run["stage_metrics"]) == {"resolution"} for run in sources)
    assert sum(run["persons_created"] for run in sources) >= 2
    assert (len(ovd.fetches), len(sota.fetches)) == (1, 1)
    assert (derived["scope"], derived["status"]) == ("derived", "completed")
    assert derived["classifications_created"] >= 1
    # Only the load moved the checkpoints: a resolution discovered nothing.
    states = SqlAlchemyMonitoringRepository(session_factory).list_source_states()
    assert {state.source_name: state.last_successful_run_id for state in states} == {
        run["source"]: run["id"] for run in loaded
    }


def test_resolution_records_how_far_it_got(session_factory: sessionmaker[Session]) -> None:
    ovd = FakeUpstream()
    ovd.publish("sidorov", SIDOROV)
    ovd.publish("petrov", PETROV)
    service = build_service(session_factory, {"ovd-info": ovd})
    service.run_source("ovd-info", with_derived=False, with_resolution=False)
    recorded: list[dict[str, object]] = []
    original = service.repository.set_stage_metrics

    def spy(run_id: int, stage: MonitoringStage, metrics: Mapping[str, Any]) -> None:
        if stage is MonitoringStage.RESOLUTION:
            recorded.append(dict(metrics))
        original(run_id, stage, metrics)

    service.repository.set_stage_metrics = spy  # type: ignore[method-assign]

    run = service.resolve_source("ovd-info")

    assert recorded[0] == {"extraction_runs": 2, "done": 0}
    assert recorded[-1]["extraction_runs"] == 2
    assert "done" not in recorded[-1]
    assert run.stage_metrics["resolution"]["extraction_runs"] == 2


def test_a_post_of_only_media_is_skipped_not_failed(session_factory: sessionmaker[Session]) -> None:
    ovd = FakeUpstream()
    ovd.publish("sidorov", SIDOROV)
    ovd.publish("photo", MEDIA_ONLY)
    service = build_service(session_factory, {"ovd-info": ovd})

    run = service.run_source("ovd-info", with_derived=False, with_resolution=False)

    assert run.status.value == "completed"
    assert (run.documents_ingested, run.documents_skipped, run.documents_failed) == (1, 1, 0)
    assert run.error_count == 0
    assert run.stage_metrics["ingestion"]["skipped_no_text"] == 1


def test_a_regular_run_stops_discovery_where_stored_posts_begin_after_a_completed_load(
    session_factory: sessionmaker[Session],
) -> None:
    ovd = FakeUpstream()
    ovd.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": ovd})

    first = service.run_source("ovd-info", with_derived=False, with_resolution=False)
    after_first = ovd.discoveries_until_known
    service.run_source("ovd-info", with_derived=False, with_resolution=False)
    after_second = ovd.discoveries_until_known
    # A load cut short stored the newest posts only: the next run must page past them.
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE monitoring_runs SET status = 'aborted' WHERE id = (SELECT max(id) FROM monitoring_runs)"
            )
        )
    service.run_source("ovd-info", with_derived=False, with_resolution=False)
    after_aborted = ovd.discoveries_until_known
    service.run_source(
        "ovd-info", trigger=MonitoringTrigger.BACKFILL, discovery_limit=5, with_derived=False
    )

    assert first.status.value == "completed"
    assert after_first == 0  # the first load of a source reads the whole window
    assert after_second == 1
    assert after_aborted == 1
    assert ovd.discoveries_until_known == 1  # a backfill goes deep on purpose
    assert ovd.discoveries == 4


def test_the_ceiling_is_for_runs_that_stop_at_what_is_stored_and_for_backfills(
    session_factory: sessionmaker[Session],
) -> None:
    """Thousands is how far a run may reach back to what it has; a run with nothing
    stored to stop at (a source's first load, the load after one that broke) would
    read that many publications."""
    ovd = FakeUpstream()
    ovd.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": ovd}, discovery_limit=5000)

    service.run_source("ovd-info", with_derived=False, with_resolution=False)
    service.run_source("ovd-info", with_derived=False, with_resolution=False)
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE monitoring_runs SET status = 'aborted' WHERE id = (SELECT max(id) FROM monitoring_runs)"
            )
        )
    service.run_source("ovd-info", with_derived=False, with_resolution=False)
    service.run_source("ovd-info", trigger=MonitoringTrigger.BACKFILL, with_derived=False)
    service.run_source(
        "ovd-info", trigger=MonitoringTrigger.BACKFILL, discovery_limit=7, with_derived=False
    )

    # First load; stops at stored; after a broken load; a backfill; a backfill with its own.
    assert ovd.limits == [500, 5000, 500, 5000, 7]


def test_purge_junk_reports_what_it_removed(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    ovd = FakeUpstream()
    ovd.publish("sidorov", SIDOROV)
    ovd.publish("exhibition", "Пётр Петров открыл выставку современного искусства.")
    build_service(session_factory, {"ovd-info": ovd}).run_source("ovd-info", with_derived=False)

    parser = argparse.ArgumentParser()
    add_cycle_arguments(parser.add_subparsers(dest="command", required=True))
    assert run_cycle_command(parser.parse_args(["purge-junk"]), session_factory)
    report = json.loads(capsys.readouterr().out)

    assert isinstance(report, dict)
    assert report["articles"] == 1


def test_every_site_gets_a_lane_and_a_host_of_many_sources_one_per_allowed_request() -> None:
    limits = FetchLimits(per_domain_concurrency=2)

    lanes = source_lanes(
        ["tg-mash", "ovd-info", "tg-rusnews", "kommersant", "tg-astrapress", "sota-vision"],
        limits,
    )

    # t.me allows 2 requests at once: two lanes of one request each, channels dealt
    # in turn, so t.me never sees more than it did from a single lane.
    assert lanes == [
        Lane(host="t.me", sources=["tg-mash", "tg-astrapress"], shared=True),
        Lane(host="t.me", sources=["tg-rusnews"], shared=True),
        Lane(host="ovd.info", sources=["ovd-info"], shared=False),
        Lane(host="www.kommersant.ru", sources=["kommersant"], shared=False),
        Lane(host="sota.vision", sources=["sota-vision"], shared=False),
    ]


def test_a_domain_limit_of_its_own_sets_how_many_lanes_its_host_gets() -> None:
    limits = FetchLimits(per_domain_concurrency=2, domain_concurrency={"t.me": 3})

    lanes = source_lanes(["tg-mash", "tg-rusnews", "tg-astrapress", "tg-bazabazon"], limits)

    assert [lane.sources for lane in lanes] == [
        ["tg-mash", "tg-bazabazon"],
        ["tg-rusnews"],
        ["tg-astrapress"],
    ]


def test_a_shared_lane_sends_one_request_at_a_time_to_its_host() -> None:
    shared = Lane(host="t.me", sources=["tg-mash"], shared=True)
    alone = Lane(host="ovd.info", sources=["ovd-info"], shared=False)
    env = {"INGESTION_DOMAIN_CONCURRENCY": "memopzk.org=1"}

    assert shared.env(env) == {"INGESTION_DOMAIN_CONCURRENCY": "memopzk.org=1,t.me=1"}
    assert shared.env({}) == {"INGESTION_DOMAIN_CONCURRENCY": "t.me=1"}
    assert alone.env(env) == {}
    limits = FetchLimits.from_env(env | shared.env(env))
    assert (limits.concurrency_for("t.me"), limits.concurrency_for("memopzk.org")) == (1, 1)


def test_a_lane_child_loads_only_its_sources_with_the_same_terms() -> None:
    args = _parse(
        "monitor",
        "--catch-up",
        "--load-only",
        "--limit",
        "20",
        "--published-from",
        "2026-10-01",
        "--published-to",
        "2026-10-16",
    )

    assert lane_arguments(args, ["tg-mash", "tg-rusnews"]) == [
        "monitor",
        "--catch-up",
        "--load-only",
        "--lane",
        "--selected-source",
        "tg-mash",
        "--selected-source",
        "tg-rusnews",
        "--limit",
        "20",
        "--published-from",
        "2026-10-01",
        "--published-to",
        "2026-10-16",
    ]


def test_a_failed_lane_outweighs_an_already_running_one() -> None:
    assert merge_lane_exit_codes([0, 0]) == 0
    assert merge_lane_exit_codes([0, MONITOR_EXIT_ALREADY_RUNNING]) == MONITOR_EXIT_ALREADY_RUNNING
    assert (
        merge_lane_exit_codes([MONITOR_EXIT_ALREADY_RUNNING, MONITOR_EXIT_FAILED, 0])
        == MONITOR_EXIT_FAILED
    )
    assert merge_lane_exit_codes([0, 2]) == 2


def test_a_lane_child_gets_its_environment_and_a_session_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cli.monitoring as monitoring_cli

    started: dict[str, Any] = {}

    class FakePopen:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            started.update(kwargs, command=command)

    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    monitoring_cli._LaneProcess(["monitor", "--lane"], {"INGESTION_DOMAIN_CONCURRENCY": "t.me=1"})

    assert started["command"][-2:] == ["monitor", "--lane"]
    assert started["env"]["INGESTION_DOMAIN_CONCURRENCY"] == "t.me=1"
    assert started["start_new_session"] is True


def test_a_parallel_load_reports_its_sources_in_the_order_they_were_given(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import cli.monitoring as monitoring_cli

    sources = ["tg-mash", "ovd-info", "tg-rusnews", "tg-astrapress"]

    class Done:
        def __init__(self, argv: list[str], env: Mapping[str, str]) -> None:
            selected = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--selected-source"]
            self._results = [{"source": source} for source in selected]

        def wait(self) -> tuple[int, list[Any]]:
            return 0, self._results

        def signal_stop(self) -> None:
            pass

        def finish_stop(self, deadline: float) -> None:
            pass

    args = _parse("monitor", "--catch-up", "--load-only")
    lanes = source_lanes(sources, FetchLimits(per_domain_concurrency=2))
    monitoring_cli._load_in_lanes(args, sources, lanes, Done)

    assert [run["source"] for run in json.loads(capsys.readouterr().out)] == sources


MAIN_SRC = APP_SRC

_LANE_CHILD = """
import os, signal, sys, time
marker, ignore = sys.argv[1], sys.argv[2] == "ignore"
if len(sys.argv) > 3:
    sys.path.insert(0, sys.argv[3])
    from cli.monitoring import LANE_PARENT_ENV, interrupt_when_parent_dies
    assert interrupt_when_parent_dies(int(os.environ[LANE_PARENT_ENV]))
def stopped(*_):
    open(marker, "w").write(str(time.monotonic()))
    sys.exit(130)
signal.signal(signal.SIGINT, signal.SIG_IGN if ignore else stopped)
print("ready", flush=True)
time.sleep(60)
"""


def _child(marker: Any, mode: str, *, src: Path | None = None) -> list[str]:
    return [sys.executable, "-c", _LANE_CHILD, str(marker), mode, *([str(src)] if src else [])]


def test_stopping_signals_every_lane_at_once_and_kills_one_that_stays(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cli.monitoring as monitoring_cli

    monkeypatch.setattr(monitoring_cli, "LANE_STOP_GRACE_SECONDS", 2.0)
    stubborn = monitoring_cli._LaneProcess([], {}, command=_child(tmp_path / "s", "ignore"))
    others = [
        monitoring_cli._LaneProcess([], {}, command=_child(tmp_path / f"o{n}", "stop"))
        for n in range(3)
    ]
    handles = [stubborn, *others]
    for handle in handles:
        assert handle._process.stdout is not None
        assert handle._process.stdout.readline().strip() == "ready"

    started = time.monotonic()
    monitoring_cli._stop_lanes(handles)
    took = time.monotonic() - started

    # The stubborn lane first in line does not hold the others' signal back.
    stopped_at = [float((tmp_path / f"o{n}").read_text()) for n in range(3)]
    assert all(at - started < 1.0 for at in stopped_at)
    assert stubborn._process.returncode == -signal.SIGKILL
    assert took < 4.0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_PDEATHSIG is Linux")
def test_a_lane_is_interrupted_when_its_parent_is_killed(tmp_path: Any) -> None:
    marker = tmp_path / "child"
    parent_code = f"""
import subprocess, sys, time
sys.path.insert(0, {str(MAIN_SRC)!r})
from cli.monitoring import _LaneProcess
lane = _LaneProcess([], {{}}, command={_child(marker, "stop", src=MAIN_SRC)!r})
print(lane._process.stdout.readline().strip(), flush=True)
time.sleep(60)
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code], stdout=subprocess.PIPE, text=True
    )
    assert parent.stdout is not None
    assert parent.stdout.readline().strip() == "ready"

    parent.kill()
    parent.wait()

    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.exists(), "the orphaned lane never got SIGINT"


def test_a_lane_whose_parent_is_already_gone_loads_nothing(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    import cli.monitoring as monitoring_cli

    monkeypatch.setenv(monitoring_cli.LANE_PARENT_ENV, "999999")
    monkeypatch.setattr(monitoring_cli, "interrupt_when_parent_dies", lambda parent: False)

    with pytest.raises(SystemExit) as stopped:
        run_monitoring_command(
            _parse("monitor", "--catch-up", "--load-only", "--lane", "--source", "ovd-info"),
            session_factory,
            build_services=lambda factory: pytest.fail("a lane without a parent built services"),
        )
    assert stopped.value.code == MONITOR_EXIT_FAILED


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_PDEATHSIG is Linux")
def test_a_refused_parent_death_signal_is_no_guarantee(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import ctypes

    import cli.monitoring as monitoring_cli

    class Refusing:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def prctl(self, option: int, value: int) -> int:
            ctypes.set_errno(1)  # EPERM, as a seccomp filter answers
            return -1

    monkeypatch.setattr(ctypes, "CDLL", Refusing)

    # The parent is alive, but nothing would stop this lane once it is not.
    assert not monitoring_cli.interrupt_when_parent_dies(os.getppid())
    assert "monitoring_lane_parent_death_signal_refused" in caplog.text

"""`monitor`, `monitor-derived`, `monitor-resolve`, `monitoring-status`,
`monitoring-findings` CLI commands."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import PETROV, SIDOROV, FakeUpstream, build_service

from application import ApplicationServices
from monitoring.cli import (
    MONITOR_EXIT_ALREADY_RUNNING,
    add_monitoring_arguments,
    run_monitoring_command,
)
from monitoring.findings import MonitoringFindingService
from monitoring.models import MonitoringStage
from monitoring.repository import SqlAlchemyMonitoringRepository


def _parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_monitoring_arguments(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(argv)


def _run(
    session_factory: sessionmaker[Session],
    upstreams: dict[str, FakeUpstream],
    capsys: pytest.CaptureFixture[str],
    *argv: str,
) -> object:
    service = build_service(session_factory, upstreams)

    def build(factory: sessionmaker[Session]) -> ApplicationServices:
        return ApplicationServices(
            session_factory=factory,
            monitoring_settings=service.settings,
            monitoring=service,
            monitoring_repository=SqlAlchemyMonitoringRepository(factory),
            findings=MonitoringFindingService(factory),
        )

    assert run_monitoring_command(_parse(*argv), session_factory, build_services=build)
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

    loaded = _run(
        session_factory, upstreams, capsys, "monitor", "--catch-up", "--load-only", *selection
    )
    resolved = _run(session_factory, upstreams, capsys, "monitor-resolve", *selection)

    # The load: two source runs, no resolution, no derived run.
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

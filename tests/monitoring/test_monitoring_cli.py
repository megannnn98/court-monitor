"""`monitor`, `monitor-derived`, `monitoring-status`, `monitoring-findings` CLI commands."""

from __future__ import annotations

import argparse
import json

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


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (("monitor", "--backfill", "--source", "ovd-info"), "--backfill requires"),
        (("monitor", "--refetch-known"), "--refetch-known requires --backfill"),
        (("monitor", "--source", "unknown"), "Unknown source: unknown"),
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

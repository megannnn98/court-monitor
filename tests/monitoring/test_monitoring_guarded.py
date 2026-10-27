"""Interruptions finish every kind of monitoring run and reach the caller."""

from unittest.mock import Mock

import pytest

from orchestrator.models import MonitoringSettings, MonitoringTrigger
from orchestrator.service import MonitoringService, PipelineModules, RunHandle


@pytest.mark.parametrize(
    ("method", "stage", "source"),
    [
        ("run_source", "discover", "ovd-info"),
        ("resolve_source", "resolve", "ovd-info"),
        ("run_derived", "_run_derived_stages", None),
    ],
)
def test_interruption_finishes_and_propagates(
    monkeypatch: pytest.MonkeyPatch, method: str, stage: str, source: str | None
) -> None:
    service = MonitoringService(Mock(spec=PipelineModules, repository=Mock()), MonitoringSettings())
    handle = RunHandle(run_id=42, source=source, trigger=MonitoringTrigger.MANUAL)
    monkeypatch.setattr(service, "start_source_run", Mock(return_value=handle))
    monkeypatch.setattr(service, "start_derived_run", Mock(return_value=handle))
    interrupted = KeyboardInterrupt("stop")
    monkeypatch.setattr(service, stage, Mock(side_effect=interrupted))
    finish = Mock()
    monkeypatch.setattr(service, "finish", finish)

    with pytest.raises(KeyboardInterrupt) as caught:
        if source is None:
            service.run_derived()
        elif method == "run_source":
            service.run_source(source)
        else:
            service.resolve_source(source)

    assert caught.value is interrupted
    expected = RunHandle(
        run_id=42,
        source=source,
        trigger=MonitoringTrigger.MANUAL,
        resolve_only=method == "resolve_source",
    )
    finish.assert_called_once_with(expected, error=interrupted)


@pytest.mark.parametrize(
    ("method", "stage", "fields"),
    [
        ("run_source", "discover", "source=ovd-info"),
        ("resolve_source", "resolve", "source=ovd-info scope=resolution"),
        ("run_derived", "_run_derived_stages", "scope=derived"),
    ],
)
def test_failure_log_preserves_fields(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    method: str,
    stage: str,
    fields: str,
) -> None:
    service = MonitoringService(Mock(spec=PipelineModules, repository=Mock()), MonitoringSettings())
    handle = RunHandle(run_id=42, source="ovd-info", trigger=MonitoringTrigger.MANUAL)
    monkeypatch.setattr(service, "start_source_run", Mock(return_value=handle))
    monkeypatch.setattr(service, "start_derived_run", Mock(return_value=handle))
    error = ValueError("failed stage")
    monkeypatch.setattr(service, stage, Mock(side_effect=error))
    finish = Mock()
    monkeypatch.setattr(service, "finish", finish)

    if method == "run_derived":
        result = service.run_derived()
    elif method == "run_source":
        result = service.run_source("ovd-info")
    else:
        result = service.resolve_source("ovd-info")

    assert result is finish.return_value
    assert [record.getMessage() for record in caplog.records] == [
        f"event=monitoring_run_failed run_id=42 {fields}"
    ]
    assert caplog.records[0].exc_info is not None
    assert caplog.records[0].exc_info[1] is error
    assert finish.call_count == 1
    assert finish.call_args.kwargs == {"error": error}

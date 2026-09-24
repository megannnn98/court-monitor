"""`/update` and `/status` against fakes: no pipeline, no database, no Telegram."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from monitoring.models import (
    FailureKind,
    MonitoringRunDetails,
    MonitoringRunItemView,
    MonitoringRunStatus,
    MonitoringRunView,
    MonitoringStage,
    MonitoringTrigger,
)
from operator_console import (
    OPERATION_DEFINITIONS,
    OperationConflictError,
    OperationParameters,
    OperationRun,
    OperationRunStatus,
)
from telegram_bot.update_service import (
    UpdateAlreadyRunning,
    UpdateService,
    UpdateStarted,
)

STARTED = datetime(2026, 9, 19, 14, 32, tzinfo=UTC)
SOURCES = ("ovd-info", "sota-vision")


class FakeOperations:
    """The operator console, minus the process: `start` records a run and returns."""

    def __init__(self) -> None:
        self.runs: list[OperationRun] = []
        self.started: list[tuple[str, OperationParameters]] = []
        self._next_id = 41

    def start(self, name: str, parameters: OperationParameters) -> OperationRun:
        live = {OperationRunStatus.PENDING, OperationRunStatus.RUNNING}
        if any(run.operation.name == name and run.status in live for run in self.runs):
            # The partial unique index of operator_operation_runs, in a fake.
            raise OperationConflictError(f"operation {name} is already running")
        self._next_id += 1
        run = OperationRun(
            id=self._next_id,
            operation=OPERATION_DEFINITIONS[name],
            parameters=parameters,
            status=OperationRunStatus.RUNNING,
            created_at=STARTED,
            started_at=STARTED,
        )
        self.runs.insert(0, run)
        self.started.append((name, parameters))
        return run

    def runs_of(self, name: str, limit: int = 20) -> list[OperationRun]:
        return [run for run in self.runs if run.operation.name == name][:limit]

    def finish(self, status: OperationRunStatus = OperationRunStatus.SUCCEEDED) -> None:
        self.runs[0].status = status
        self.runs[0].finished_at = STARTED + timedelta(minutes=6)


class FakeMonitoringRuns:
    def __init__(self, runs: list[MonitoringRunView] | None = None) -> None:
        self.runs = runs or []
        self.details: dict[int, MonitoringRunDetails] = {}

    def list_runs_started_between(
        self, started: datetime, finished: datetime | None, *, trigger: MonitoringTrigger
    ) -> list[MonitoringRunView]:
        return [run for run in self.runs if run.started_at >= started]

    def get_run_details(self, run_id: int) -> MonitoringRunDetails | None:
        return self.details.get(run_id)


def monitoring_run(
    run_id: int,
    *,
    source: str | None,
    status: MonitoringRunStatus = MonitoringRunStatus.COMPLETED,
    **counters: int,
) -> MonitoringRunView:
    return MonitoringRunView(
        id=run_id,
        scope=f"source:{source}" if source else "derived",
        source=source,
        trigger_type=MonitoringTrigger.MANUAL,
        status=status,
        started_at=STARTED + timedelta(minutes=run_id),
        heartbeat_at=STARTED + timedelta(minutes=run_id),
        finished_at=STARTED + timedelta(minutes=run_id + 1),
        duration_seconds=60.0,
        documents_discovered=counters.get("documents_discovered", 0),
        documents_ingested=counters.get("documents_ingested", 0),
        documents_skipped=0,
        documents_failed=0,
        articles_extracted=counters.get("articles_extracted", 0),
        events_created=counters.get("events_created", 0),
        persons_created=counters.get("persons_created", 0),
        persons_linked=counters.get("persons_linked", 0),
        person_reviews_created=counters.get("person_reviews_created", 0),
        classifications_created=0,
        rf_matches_created=0,
        semantic_entities_indexed=0,
        findings_created=0,
        error_count=counters.get("error_count", 0),
        rf_snapshot_id=None,
        stage_metrics={},
        error_message=None,
    )


def service(
    operations: FakeOperations, monitoring: FakeMonitoringRuns | None = None
) -> UpdateService:
    return UpdateService(
        operations,
        monitoring or FakeMonitoringRuns(),
        enabled_sources=SOURCES,
        discovery_limit=50,
    )


def test_update_starts_the_monitor_operation() -> None:
    operations = FakeOperations()

    started = service(operations).start_update()

    assert isinstance(started, UpdateStarted)
    assert started.run.id == 42
    assert started.sources == SOURCES
    assert operations.started == [("monitor", OperationParameters(limit=50))]


def test_update_returns_before_the_pipeline_finishes() -> None:
    operations = FakeOperations()

    started = service(operations).start_update()

    # The run is live when the handler already has its answer: nothing was awaited.
    assert isinstance(started, UpdateStarted)
    assert started.run.status is OperationRunStatus.RUNNING
    assert started.run.finished_at is None


def test_a_second_update_does_not_start_another_run() -> None:
    operations = FakeOperations()
    bot = service(operations)
    bot.start_update()

    again = bot.start_update()

    assert isinstance(again, UpdateAlreadyRunning)
    assert again.run.id == 42
    assert len(operations.started) == 1


def test_update_is_allowed_again_once_the_previous_one_finished() -> None:
    operations = FakeOperations()
    bot = service(operations)
    bot.start_update()
    operations.finish()

    started = bot.start_update()

    assert isinstance(started, UpdateStarted)
    assert started.run.id == 43


def test_runs_of_other_operations_do_not_hide_the_live_update() -> None:
    operations = FakeOperations()
    bot = service(operations)
    bot.start_update()
    # 30 runs of other operations, enough to push the monitor run out of any
    # fixed-size window over the whole table.
    for index in range(30):
        operations.runs.insert(
            0,
            OperationRun(
                id=1000 + index,
                operation=OPERATION_DEFINITIONS["extract-entities"],
                parameters=OperationParameters(limit=50),
                status=OperationRunStatus.SUCCEEDED,
                created_at=STARTED,
                started_at=STARTED,
                finished_at=STARTED,
            ),
        )

    again = bot.start_update()
    status = bot.last_update()

    assert isinstance(again, UpdateAlreadyRunning)
    assert again.run.id == 42
    assert status is not None and status.run.id == 42


def test_a_conflict_without_a_live_run_is_not_swallowed() -> None:
    class AlwaysConflicting(FakeOperations):
        def start(self, name: str, parameters: OperationParameters) -> OperationRun:
            raise OperationConflictError("operation monitor is already running")

    with pytest.raises(OperationConflictError):
        service(AlwaysConflicting()).start_update()


def test_status_is_none_without_any_update() -> None:
    assert service(FakeOperations()).last_update() is None


def test_status_sums_the_monitoring_runs_of_the_operation() -> None:
    operations = FakeOperations()
    bot = service(
        operations,
        FakeMonitoringRuns(
            [
                monitoring_run(
                    1,
                    source="ovd-info",
                    documents_discovered=34,
                    documents_ingested=12,
                    articles_extracted=12,
                    events_created=27,
                    persons_created=8,
                    persons_linked=5,
                    person_reviews_created=3,
                ),
                monitoring_run(2, source="sota-vision", documents_discovered=10),
                monitoring_run(3, source=None, persons_created=0),
            ]
        ),
    )
    bot.start_update()

    status = bot.last_update()

    assert status is not None
    assert (status.sources_done, status.sources_total) == (2, 2)
    assert status.derived_done
    assert status.documents_discovered == 44
    assert status.documents_ingested == 12
    assert status.articles_extracted == 12
    assert status.events_created == 27
    assert status.persons_created == 8
    assert status.reviews_created == 3


def test_status_uses_the_temporary_source_selection_of_a_web_run() -> None:
    operations = FakeOperations()
    operations.runs.append(
        OperationRun(
            id=99,
            operation=OPERATION_DEFINITIONS["monitor"],
            parameters=OperationParameters(sources=["sota-vision"], limit=50),
            status=OperationRunStatus.SUCCEEDED,
            created_at=STARTED,
            started_at=STARTED,
            finished_at=STARTED + timedelta(minutes=6),
        )
    )
    bot = service(operations, FakeMonitoringRuns([monitoring_run(1, source="sota-vision")]))

    status = bot.last_update()

    assert status is not None
    assert (status.sources_done, status.sources_total) == (1, 1)


def test_status_names_the_source_being_processed() -> None:
    operations = FakeOperations()
    bot = service(
        operations,
        FakeMonitoringRuns(
            [
                monitoring_run(1, source="ovd-info"),
                monitoring_run(2, source="sota-vision", status=MonitoringRunStatus.RUNNING),
            ]
        ),
    )
    bot.start_update()

    status = bot.last_update()

    assert status is not None
    assert status.sources_done == 1
    assert status.current_source == "sota-vision"
    assert not status.derived_done


def test_status_reports_failures_by_stage_and_kind_without_a_traceback() -> None:
    operations = FakeOperations()
    failing = monitoring_run(1, source="ovd-info", error_count=2)
    monitoring = FakeMonitoringRuns([failing])
    monitoring.details[1] = MonitoringRunDetails(
        run=failing,
        items=[
            MonitoringRunItemView(
                id=index,
                run_id=1,
                stage=MonitoringStage.INGESTION,
                entity_type="source_reference",
                entity_id=None,
                external_ref="https://news.example/1",
                status="failed",
                failure_kind=FailureKind.RETRYABLE,
                error_type="ReadTimeout",
                error_message="traceback: secret connection string",
                created_at=STARTED,
            )
            for index in (1, 2)
        ],
    )
    bot = service(operations, monitoring)
    bot.start_update()

    status = bot.last_update()

    assert status is not None
    assert status.error_count == 2
    assert len(status.failures) == 1
    failure = status.failures[0]
    assert (failure.stage, failure.count, failure.kind) == ("ingestion", 2, FailureKind.RETRYABLE)
    assert failure.message == "ReadTimeout"
    assert "secret" not in failure.message

"""Routine operations started from the operator console, and their runs.

PostgreSQL is the source of truth for runs (`operator_operation_runs`): every API
process sees the same runs, a restart keeps the history, and a partial unique index
allows one live run per operation across processes. The run itself is a subprocess of
the CLI, built from an allowlist and started without a shell, in a background thread of
the process that accepted it; it keeps a heartbeat, and a run whose heartbeat stopped
(its process died) is marked `interrupted` the next time runs are read or started.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import OperatorOperationRunRecord
from sources.source_registry import SOURCES

logger = logging.getLogger("operator_console")

# stdout and stderr keep their last characters: the end of a log says how it ended.
OUTPUT_LIMIT = 20_000
HEARTBEAT_INTERVAL = timedelta(seconds=15)
# A live run whose heartbeat is older than this lost its process.
STALE_AFTER = timedelta(minutes=5)
ACTIVE_RUN_INDEX = "uq_operator_operation_runs_active_operation"


class OperationRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


LIVE_STATUSES = (OperationRunStatus.PENDING.value, OperationRunStatus.RUNNING.value)


class OperationParameters(BaseModel):
    source: str | None = None
    limit: int | None = Field(default=None, ge=1, le=100_000)
    workers: int | None = Field(default=None, ge=1, le=32)


@dataclass(frozen=True)
class OperationDefinition:
    name: str
    title: str
    description: str
    next_action: str
    warning: str
    default_parameters: OperationParameters


@dataclass
class OperationRun:
    id: int
    operation: OperationDefinition
    parameters: OperationParameters
    status: OperationRunStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    command: list[str] = field(default_factory=list)
    return_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    heartbeat_at: datetime | None = None
    worker_id: str | None = None

    @property
    def duration_seconds(self) -> float | None:
        end = self.finished_at
        if self.started_at is None or end is None:
            return None
        return round((end - self.started_at).total_seconds(), 3)


OPERATION_DEFINITIONS: dict[str, OperationDefinition] = {
    "monitor": OperationDefinition(
        name="monitor",
        title="Докачать новые публикации",
        description=(
            "Для каждого источника (или одного выбранного) загрузить публикации, появившиеся "
            "после прошлой загрузки, извлечь людей и события; затем один раз "
            "классифицировать, сверить с перечнем РФМ и обновить находки."
        ),
        next_action="Оставьте «Все источники» и limit 50, затем подтвердите run.",
        warning=(
            "Операция ходит в сеть по всем источникам и пишет в живую базу; "
            "первая докачка после перерыва может идти десятки минут."
        ),
        default_parameters=OperationParameters(limit=50),
    ),
    "discover-and-ingest": OperationDefinition(
        name="discover-and-ingest",
        title="Загрузить публикации",
        description="Найти новые публикации выбранного источника и сохранить их как ParsedArticle.",
        next_action="Выберите источник и небольшой limit, затем подтвердите run.",
        warning="Операция ходит в сеть и пишет новые source documents/articles в живую базу.",
        default_parameters=OperationParameters(source="ovd-info", limit=50),
    ),
    "extract-entities": OperationDefinition(
        name="extract-entities",
        title="Извлечь сущности и события",
        description="Запустить extraction по статьям, которые ещё не обработаны текущими версиями extractor/normalizer.",
        next_action="Укажите источник или оставьте все источники, затем подтвердите run.",
        warning="Операция пишет extraction runs, mentions и events в живую базу.",
        default_parameters=OperationParameters(limit=500),
    ),
    "resolve-people": OperationDefinition(
        name="resolve-people",
        title="Разрешить упоминания в Person",
        description="Связать person mentions с canonical Person или отправить спорные случаи в ER-review.",
        next_action="Поставьте limit больше числа свежих статей, затем подтвердите run.",
        warning="Операция создаёт/линкует Person и пополняет очередь ER-review.",
        default_parameters=OperationParameters(limit=10_000, workers=1),
    ),
    "classify-persecution": OperationDefinition(
        name="classify-persecution",
        title="Классифицировать преследование",
        description="Запустить rule-based классификацию активных Person.",
        next_action="Проверьте limit и подтвердите run.",
        warning="Операция пишет persecution classifications в живую базу.",
        default_parameters=OperationParameters(limit=10_000),
    ),
}


class OperationConflictError(Exception):
    pass


class OperationNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class ProcessResult:
    return_code: int
    stdout: str
    stderr: str


# Runs the command; calls `heartbeat` at least every HEARTBEAT_INTERVAL while it lives.
ProcessRunner = Callable[[list[str], Callable[[], None]], ProcessResult]
# Hands the run's work to something that executes it outside the HTTP request.
Executor = Callable[[Callable[[], None]], None]


def _thread_executor(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


def _run_process(command: list[str], heartbeat: Callable[[], None]) -> ProcessResult:
    """The command without a shell, its output captured while the heartbeat goes on."""
    process = subprocess.Popen(
        command,
        cwd=_repo_root(),
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=HEARTBEAT_INTERVAL.total_seconds())
            except subprocess.TimeoutExpired:
                heartbeat()
                continue
            return ProcessResult(process.returncode, stdout, stderr)
    except BaseException:
        # The run is about to be recorded as ended: its process must not live on unseen.
        process.kill()
        process.wait()
        raise


class OperationRegistry:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        executor: Executor = _thread_executor,
        process_runner: ProcessRunner = _run_process,
        stale_after: timedelta = STALE_AFTER,
        worker_id: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._executor = executor
        self._process_runner = process_runner
        self._stale_after = stale_after
        self._worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"

    def definitions(self) -> list[OperationDefinition]:
        return list(OPERATION_DEFINITIONS.values())

    def definition(self, name: str) -> OperationDefinition:
        try:
            return OPERATION_DEFINITIONS[name]
        except KeyError:
            raise OperationNotFoundError(name) from None

    def list_runs(self, limit: int = 100) -> list[OperationRun]:
        self.interrupt_stale_runs()
        with self._session_factory() as session:
            records = session.scalars(
                select(OperatorOperationRunRecord)
                .order_by(OperatorOperationRunRecord.id.desc())
                .limit(limit)
            ).all()
            return [_to_run(record) for record in records]

    def runs_of(self, name: str, limit: int = 20) -> list[OperationRun]:
        """Runs of one operation, newest first: a caller after `monitor` must not be
        pushed out of the window by runs of other operations."""
        self.interrupt_stale_runs()
        with self._session_factory() as session:
            records = session.scalars(
                select(OperatorOperationRunRecord)
                .where(OperatorOperationRunRecord.operation_name == name)
                .order_by(OperatorOperationRunRecord.id.desc())
                .limit(limit)
            ).all()
            return [_to_run(record) for record in records]

    def get(self, run_id: int) -> OperationRun:
        self.interrupt_stale_runs()
        with self._session_factory() as session:
            record = session.get(OperatorOperationRunRecord, run_id)
            if record is None:
                raise OperationNotFoundError(str(run_id))
            return _to_run(record)

    def prepare_parameters(
        self, definition: OperationDefinition, parameters: OperationParameters
    ) -> OperationParameters:
        return self._with_defaults(definition, parameters)

    def start(self, name: str, parameters: OperationParameters) -> OperationRun:
        """Record a pending run and hand it to the executor; returns without waiting."""
        definition = self.definition(name)
        parameters = self._with_defaults(definition, parameters)
        command = _command_for(definition.name, parameters)
        self.interrupt_stale_runs()
        try:
            with self._session_factory.begin() as session:
                record = OperatorOperationRunRecord(
                    operation_name=name,
                    parameters=parameters.model_dump(),
                    command=command,
                    status=OperationRunStatus.PENDING.value,
                )
                session.add(record)
                session.flush()
                run = _to_run(record)
        except IntegrityError as exc:
            # Only the partial unique index means another process has a live run of it.
            if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) != (
                ACTIVE_RUN_INDEX
            ):
                raise
            raise OperationConflictError(f"operation {name} is already running") from exc
        self._executor(lambda: self._execute(run.id))
        return run

    def interrupt_stale_runs(self) -> list[int]:
        """Live runs whose heartbeat stopped: their process died, they will never finish."""
        with self._session_factory.begin() as session:
            last_sign_of_life = func.coalesce(
                OperatorOperationRunRecord.heartbeat_at, OperatorOperationRunRecord.created_at
            )
            interrupted = list(
                session.scalars(
                    update(OperatorOperationRunRecord)
                    .where(
                        OperatorOperationRunRecord.status.in_(LIVE_STATUSES),
                        last_sign_of_life < func.now() - self._stale_after,
                    )
                    .values(
                        status=OperationRunStatus.INTERRUPTED.value,
                        finished_at=func.now(),
                        error=(
                            f"no heartbeat for {int(self._stale_after.total_seconds())} s: "
                            "the process running it stopped"
                        ),
                    )
                    .returning(OperatorOperationRunRecord.id)
                ).all()
            )
        for run_id in interrupted:
            logger.warning("event=operation_run_interrupted run_id=%s", run_id)
        return interrupted

    @staticmethod
    def _with_defaults(
        definition: OperationDefinition, parameters: OperationParameters
    ) -> OperationParameters:
        defaults = definition.default_parameters.model_dump()
        provided = parameters.model_dump(exclude_none=True)
        return OperationParameters(**(defaults | provided))

    def _execute(self, run_id: int) -> None:
        if not self._claim(run_id):
            return
        try:
            result = self._process_runner(self._command(run_id), lambda: self._heartbeat(run_id))
        except BaseException as exc:  # noqa: BLE001 - recorded for operator inspection
            self._finish_or_log(
                run_id,
                status=OperationRunStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
            return
        self._finish_or_log(
            run_id,
            status=(
                OperationRunStatus.SUCCEEDED
                if result.return_code == 0
                else OperationRunStatus.FAILED
            ),
            return_code=result.return_code,
            stdout=_tail(result.stdout),
            stderr=_tail(result.stderr),
        )

    def _command(self, run_id: int) -> list[str]:
        with self._session_factory() as session:
            record = session.get(OperatorOperationRunRecord, run_id)
            assert record is not None
            return list(record.command)

    def _claim(self, run_id: int) -> bool:
        """pending → running, only if the run is still pending (not interrupted meanwhile)."""
        with self._session_factory.begin() as session:
            claimed = session.execute(
                update(OperatorOperationRunRecord)
                .where(
                    OperatorOperationRunRecord.id == run_id,
                    OperatorOperationRunRecord.status == OperationRunStatus.PENDING.value,
                )
                .values(
                    status=OperationRunStatus.RUNNING.value,
                    started_at=func.now(),
                    heartbeat_at=func.now(),
                    worker_id=self._worker_id,
                )
                .returning(OperatorOperationRunRecord.id)
            ).first()
        return claimed is not None

    def _heartbeat(self, run_id: int) -> None:
        # A missed beat is not the end of the run: the process goes on, and a database
        # that stays away makes the run stale, then interrupted.
        try:
            with self._session_factory.begin() as session:
                session.execute(self._running_here(run_id).values(heartbeat_at=func.now()))
        except SQLAlchemyError:
            logger.exception("event=operation_run_heartbeat_failed run_id=%s", run_id)

    def _finish_or_log(self, run_id: int, *, status: OperationRunStatus, **values: Any) -> None:
        # Unrecorded, the run turns interrupted as stale; the log keeps the real ending.
        try:
            self._finish(run_id, status=status, **values)
        except Exception:
            logger.exception(
                "event=operation_run_finish_failed run_id=%s status=%s return_code=%s",
                run_id,
                status.value,
                values.get("return_code"),
            )

    def _finish(self, run_id: int, *, status: OperationRunStatus, **values: Any) -> None:
        # Fencing: a run interrupted as stale while its process lived keeps `interrupted`.
        with self._session_factory.begin() as session:
            session.execute(
                self._running_here(run_id).values(
                    status=status.value, finished_at=func.now(), heartbeat_at=func.now(), **values
                )
            )

    def _running_here(self, run_id: int) -> Any:
        return update(OperatorOperationRunRecord).where(
            OperatorOperationRunRecord.id == run_id,
            OperatorOperationRunRecord.status == OperationRunStatus.RUNNING.value,
            OperatorOperationRunRecord.worker_id == self._worker_id,
        )


def _to_run(record: OperatorOperationRunRecord) -> OperationRun:
    definition = OPERATION_DEFINITIONS.get(record.operation_name) or OperationDefinition(
        name=record.operation_name,
        title=record.operation_name,
        description="",
        next_action="",
        warning="",
        default_parameters=OperationParameters(),
    )
    return OperationRun(
        id=record.id,
        operation=definition,
        parameters=OperationParameters(**record.parameters),
        status=OperationRunStatus(record.status),
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        command=list(record.command),
        return_code=record.return_code,
        stdout=record.stdout,
        stderr=record.stderr,
        error=record.error,
        heartbeat_at=record.heartbeat_at,
        worker_id=record.worker_id,
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _tail(text: str, *, limit: int = OUTPUT_LIMIT) -> str:
    return text[-limit:]


def _command_for(name: str, parameters: OperationParameters) -> list[str]:
    command = [sys.executable, str(_repo_root() / "src" / "main.py"), name]
    if name == "monitor":
        command.append("--catch-up")
        if parameters.source:
            command += ["--source", _require_source(parameters.source)]
        command += ["--limit", str(_require_limit(parameters.limit))]
    elif name == "discover-and-ingest":
        source = _require_source(parameters.source)
        command += ["--source", source, "--limit", str(_require_limit(parameters.limit))]
    elif name == "extract-entities":
        if parameters.source:
            command += ["--source", _require_source(parameters.source)]
        command += ["--limit", str(_require_limit(parameters.limit))]
    elif name == "resolve-people":
        command += ["--limit", str(_require_limit(parameters.limit))]
        command += ["--workers", str(parameters.workers or 1)]
    elif name == "classify-persecution":
        command += ["--limit", str(_require_limit(parameters.limit))]
    else:
        raise OperationNotFoundError(name)
    return command


def _require_source(source: str | None) -> str:
    if source not in SOURCES:
        raise ValueError(f"unknown source: {source}")
    return source


def _require_limit(limit: int | None) -> int:
    if limit is None:
        raise ValueError("limit is required")
    return limit


def operation_run_to_dict(run: OperationRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "operation": run.operation.name,
        "title": run.operation.title,
        "parameters": run.parameters.model_dump(),
        "status": run.status.value,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "duration_seconds": run.duration_seconds,
        "command": run.command,
        "return_code": run.return_code,
        "stdout": run.stdout,
        "stderr": run.stderr,
        "error": run.error,
    }

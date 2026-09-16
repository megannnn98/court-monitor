from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from sources.source_registry import SOURCES


class OperationRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


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

    @property
    def duration_seconds(self) -> float | None:
        end = self.finished_at
        if self.started_at is None or end is None:
            return None
        return round((end - self.started_at).total_seconds(), 3)


OPERATION_DEFINITIONS: dict[str, OperationDefinition] = {
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


class OperationRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 1
        self._runs: dict[int, OperationRun] = {}

    def definitions(self) -> list[OperationDefinition]:
        return list(OPERATION_DEFINITIONS.values())

    def definition(self, name: str) -> OperationDefinition:
        try:
            return OPERATION_DEFINITIONS[name]
        except KeyError:
            raise OperationNotFoundError(name) from None

    def list_runs(self) -> list[OperationRun]:
        with self._lock:
            return sorted(self._runs.values(), key=lambda run: run.id, reverse=True)

    def get(self, run_id: int) -> OperationRun:
        with self._lock:
            try:
                return self._runs[run_id]
            except KeyError:
                raise OperationNotFoundError(str(run_id)) from None

    def prepare_parameters(
        self, definition: OperationDefinition, parameters: OperationParameters
    ) -> OperationParameters:
        return self._with_defaults(definition, parameters)

    def start(self, name: str, parameters: OperationParameters) -> OperationRun:
        definition = self.definition(name)
        parameters = self._with_defaults(definition, parameters)
        command = _command_for(definition.name, parameters)
        with self._lock:
            active = any(
                run.operation.name == name
                and run.status in {OperationRunStatus.PENDING, OperationRunStatus.RUNNING}
                for run in self._runs.values()
            )
            if active:
                raise OperationConflictError(f"operation {name} is already running")
            run = OperationRun(
                id=self._next_id,
                operation=definition,
                parameters=parameters,
                status=OperationRunStatus.PENDING,
                created_at=datetime.now(UTC),
                command=command,
            )
            self._runs[run.id] = run
            self._next_id += 1
        thread = threading.Thread(target=self._execute, args=(run.id,), daemon=True)
        thread.start()
        return run

    @staticmethod
    def _with_defaults(
        definition: OperationDefinition, parameters: OperationParameters
    ) -> OperationParameters:
        defaults = definition.default_parameters.model_dump()
        provided = parameters.model_dump(exclude_none=True)
        return OperationParameters(**(defaults | provided))

    def _execute(self, run_id: int) -> None:
        with self._lock:
            run = self._runs[run_id]
            run.status = OperationRunStatus.RUNNING
            run.started_at = datetime.now(UTC)
        try:
            completed = subprocess.run(
                run.command,
                cwd=_repo_root(),
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )
            with self._lock:
                run.return_code = completed.returncode
                run.stdout = _tail(completed.stdout)
                run.stderr = _tail(completed.stderr)
                run.status = (
                    OperationRunStatus.SUCCEEDED
                    if completed.returncode == 0
                    else OperationRunStatus.FAILED
                )
                run.finished_at = datetime.now(UTC)
        except BaseException as exc:  # noqa: BLE001 - recorded for operator inspection
            with self._lock:
                run.status = OperationRunStatus.FAILED
                run.error = f"{type(exc).__name__}: {exc}"
                run.finished_at = datetime.now(UTC)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _tail(text: str, *, limit: int = 20_000) -> str:
    return text[-limit:]


def _command_for(name: str, parameters: OperationParameters) -> list[str]:
    command = [sys.executable, str(_repo_root() / "src" / "main.py"), name]
    if name == "discover-and-ingest":
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

"""Stored investigation runs: what was asked, how far it got, what it found.

The store is a port: the graph hands it plain JSON and knows no table. A run is saved
when it is planned, after every round's coverage (its progress: the searches made and
the evidence they returned) and when it ends (its result). A run that a process failure
interrupted is resumed from its last progress: the searches made are not made again,
and their evidence — local or from a source outside — is read from the store, not
fetched anew.

What is stored is what the result holds: the question, the plan, the evidence quotes
and the usage. Never a prompt, a model's raw answer, a key or a whole publication.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

RunStatus = Literal[
    "running",
    "completed",
    "partial",
    "clarification_required",
    "review_required",
    "failed",
]
FINISHED: frozenset[str] = frozenset(
    {"completed", "partial", "clarification_required", "review_required", "failed"}
)


def new_run_id() -> str:
    return uuid.uuid4().hex


class StoredRun(Protocol):
    """A stored run as the store reads it back."""

    @property
    def run_id(self) -> str: ...
    @property
    def question(self) -> str: ...
    @property
    def status(self) -> str: ...
    @property
    def scenario(self) -> str | None: ...
    @property
    def round(self) -> int: ...
    @property
    def created_at(self) -> datetime: ...
    @property
    def updated_at(self) -> datetime: ...
    @property
    def request(self) -> dict[str, Any]: ...
    @property
    def usage(self) -> dict[str, Any]: ...
    @property
    def progress(self) -> dict[str, Any] | None: ...
    @property
    def result(self) -> dict[str, Any] | None: ...
    @property
    def failure(self) -> str | None: ...


@dataclass
class MemoryRun:
    run_id: str
    question: str
    status: str
    scenario: str | None
    round: int
    created_at: datetime
    updated_at: datetime
    # The request as given: local only, sources, scenario, the limits it lowered.
    request: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    # The state to resume from (`state.dump_state` of the resumable keys).
    progress: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    failure: str | None = None


class RunStore(Protocol):
    def create(self, run_id: str, *, question: str, request: dict[str, Any]) -> None: ...

    def save_progress(
        self,
        run_id: str,
        *,
        scenario: str | None,
        round_: int,
        usage: dict[str, Any],
        progress: dict[str, Any],
    ) -> None: ...

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        scenario: str | None,
        usage: dict[str, Any],
        result: dict[str, Any],
        failure: str | None = None,
    ) -> None: ...

    def load(self, run_id: str) -> StoredRun | None: ...


class InMemoryRunStore:
    """A store for tests and for a process that keeps nothing."""

    def __init__(self) -> None:
        self._runs: dict[str, MemoryRun] = {}
        self._lock = threading.Lock()

    def create(self, run_id: str, *, question: str, request: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        with self._lock:
            self._runs[run_id] = MemoryRun(
                run_id=run_id,
                question=question,
                status="running",
                scenario=None,
                round=0,
                created_at=now,
                updated_at=now,
                request=dict(request),
            )

    def save_progress(
        self,
        run_id: str,
        *,
        scenario: str | None,
        round_: int,
        usage: dict[str, Any],
        progress: dict[str, Any],
    ) -> None:
        with self._lock:
            run = self._runs[run_id]
            run.scenario, run.round, run.usage = scenario, round_, dict(usage)
            run.progress = progress
            run.updated_at = datetime.now(UTC)

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        scenario: str | None,
        usage: dict[str, Any],
        result: dict[str, Any],
        failure: str | None = None,
    ) -> None:
        with self._lock:
            run = self._runs[run_id]
            run.status = status
            run.scenario, run.usage, run.result, run.failure = scenario, usage, result, failure
            run.progress = None
            run.updated_at = datetime.now(UTC)

    def load(self, run_id: str) -> MemoryRun | None:
        with self._lock:
            return self._runs.get(run_id)

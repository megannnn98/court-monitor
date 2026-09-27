"""Termux-only runtime helpers: desktop is inert, operations hold a wake lock."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from phone_runtime import termux_operation_wake_lock


def test_desktop_operation_does_not_call_termux_tools() -> None:
    calls: list[list[str]] = []

    with termux_operation_wake_lock(
        env={},
        find_command=lambda _name: "/bin/should-not-run",
        run_command=lambda command: calls.append(command) or 0,
    ):
        calls.append(["operation"])

    assert calls == [["operation"]]


def test_termux_operation_releases_wake_lock_after_failure() -> None:
    calls: list[list[str]] = []

    with (
        pytest.raises(RuntimeError, match="failed operation"),
        termux_operation_wake_lock(
            env={"TERMUX_VERSION": "0.118"},
            find_command=lambda name: f"/termux/bin/{name}",
            run_command=lambda command: calls.append(command) or 0,
        ),
    ):
        calls.append(["operation"])
        raise RuntimeError("failed operation")

    assert calls == [
        ["/termux/bin/termux-wake-lock"],
        ["operation"],
        ["/termux/bin/termux-wake-unlock"],
    ]


def test_failed_wake_lock_is_not_released_as_if_it_were_acquired() -> None:
    calls: list[list[str]] = []

    with termux_operation_wake_lock(
        env={"TERMUX_VERSION": "0.118"},
        find_command=lambda name: f"/termux/bin/{name}",
        run_command=lambda command: calls.append(command) or 1,
    ):
        calls.append(["operation"])

    assert calls == [["/termux/bin/termux-wake-lock"], ["operation"]]


def test_registry_wraps_the_process_in_its_runtime_context(
    session_factory: object,
) -> None:
    # The DB-backed registry contract is covered in test_operation_runs. This focused
    # test only guards the ordering seam added for platform lifecycle helpers.
    from operator_console import (
        Heartbeat,
        OperationParameters,
        OperationRegistry,
        ProcessResult,
    )

    events: list[str] = []

    @contextmanager
    def wake_lock() -> Iterator[None]:
        events.append("lock")
        try:
            yield
        finally:
            events.append("unlock")

    def process(_command: list[str], _heartbeat: Heartbeat) -> ProcessResult:
        events.append("process")
        return ProcessResult(0, "", "")

    registry = OperationRegistry(
        session_factory,  # type: ignore[arg-type]
        executor=lambda work: work(),
        process_runner=process,
        operation_context=wake_lock,
    )
    registry.start("classify-persecution", OperationParameters(limit=1))

    assert events == ["lock", "process", "unlock"]

"""Finished steps of the manual pipeline, for tests that need a later step's turn."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from operator_console import OperationParameters, OperationRegistry

_WITH_SOURCES = ("load", "resolve")


def finish_steps(
    session_factory: sessionmaker[Session],
    registry: OperationRegistry,
    *modes: str,
    status: str = "succeeded",
) -> list[int]:
    """Record a run of each mode, in order, ended with `status`; their ids."""
    ids: list[int] = []
    for mode in modes:
        run = registry.start(
            "monitor",
            OperationParameters(
                mode=mode,  # type: ignore[arg-type]
                sources=["ovd-info"] if mode in _WITH_SOURCES else None,
            ),
        )
        with session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE operator_operation_runs SET status = :status, "
                    "started_at = now(), finished_at = now() WHERE id = :id"
                ),
                {"status": status, "id": run.id},
            )
        ids.append(run.id)
    return ids

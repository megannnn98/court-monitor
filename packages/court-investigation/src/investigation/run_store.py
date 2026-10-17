"""`investigation.runs.RunStore` over the `investigation_runs` table.

Shared storage: it reads and writes the JSON the investigation hands it and knows none
of its types.

A run is claimed with a PostgreSQL advisory lock held by a connection of its own for as
long as the process works on it: a second process asking for the same run is refused,
and a process that dies lets go with its connection. Progress and the result are written
only to a run still `running`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from investigation.storage.models import InvestigationRunRecord

# One lock per run id, in the advisory lock space; `hashtextextended` gives the bigint.
_TRY_LOCK = text("SELECT pg_try_advisory_lock(hashtextextended(:run_id, 0))")
_UNLOCK = text("SELECT pg_advisory_unlock(hashtextextended(:run_id, 0))")


@dataclass(frozen=True)
class InvestigationRunRow:
    run_id: str
    question: str
    status: str
    scenario: str | None
    round: int
    created_at: datetime
    updated_at: datetime
    request: dict[str, Any]
    usage: dict[str, Any]
    progress: dict[str, Any] | None
    result: dict[str, Any] | None
    failure: str | None


class SqlAlchemyInvestigationRuns:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        # The connections that hold the advisory locks of the runs this process works on.
        self._claims: dict[str, Connection] = {}
        self._claims_lock = threading.Lock()

    def claim(self, run_id: str) -> bool:
        with self._session_factory() as session:
            # A connection of its own, out of the pool for the run's whole time.
            connection = session.get_bind().engine.connect()
        try:
            taken = connection.execute(_TRY_LOCK, {"run_id": run_id}).scalar()
            # The lock is the session's, not the transaction's: end the transaction so
            # the connection does not sit «idle in transaction» for the whole run.
            connection.commit()
        except BaseException:
            connection.close()
            raise
        if not taken:
            connection.close()
            return False
        with self._claims_lock:
            self._claims[run_id] = connection
        return True

    def release(self, run_id: str) -> None:
        with self._claims_lock:
            connection = self._claims.pop(run_id, None)
        if connection is None:
            return
        try:
            connection.execute(_UNLOCK, {"run_id": run_id})
            connection.commit()
        finally:
            connection.close()

    def update_progress(
        self, run_id: str, change: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        with self._session_factory.begin() as session:
            row = session.scalar(
                select(InvestigationRunRecord)
                .where(
                    InvestigationRunRecord.run_id == run_id,
                    InvestigationRunRecord.status == "running",
                )
                .with_for_update()
            )
            if row is None or row.progress is None:
                return
            row.progress = change(dict(row.progress))
            row.updated_at = datetime.now(UTC)

    def create(self, run_id: str, *, question: str, request: dict[str, Any]) -> None:
        with self._session_factory.begin() as session:
            session.add(
                InvestigationRunRecord(
                    run_id=run_id,
                    question=question,
                    status="running",
                    request=request,
                    usage={},
                )
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
        with self._session_factory.begin() as session:
            session.execute(
                update(InvestigationRunRecord)
                .where(
                    InvestigationRunRecord.run_id == run_id,
                    InvestigationRunRecord.status == "running",
                )
                .values(
                    scenario=scenario,
                    round=round_,
                    usage=usage,
                    progress=progress,
                    updated_at=datetime.now(UTC),
                )
            )

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
        with self._session_factory.begin() as session:
            session.execute(
                update(InvestigationRunRecord)
                .where(
                    InvestigationRunRecord.run_id == run_id,
                    InvestigationRunRecord.status == "running",
                )
                .values(
                    status=status,
                    scenario=scenario,
                    usage=usage,
                    result=result,
                    # A finished run is not resumed: what it would resume from goes.
                    progress=None,
                    failure=failure,
                    updated_at=datetime.now(UTC),
                )
            )

    def load(self, run_id: str) -> InvestigationRunRow | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(InvestigationRunRecord).where(InvestigationRunRecord.run_id == run_id)
            )
            if row is None:
                return None
            return InvestigationRunRow(
                run_id=row.run_id,
                question=row.question,
                status=row.status,
                scenario=row.scenario,
                round=row.round,
                created_at=row.created_at,
                updated_at=row.updated_at,
                request=dict(row.request or {}),
                usage=dict(row.usage or {}),
                progress=row.progress,
                result=row.result,
                failure=row.failure,
            )

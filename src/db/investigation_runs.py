"""`investigation.runs.RunStore` over the `investigation_runs` table.

Shared storage: it reads and writes the JSON the investigation hands it and knows none
of its types.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from db.models.investigation import InvestigationRunRecord


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
                .where(InvestigationRunRecord.run_id == run_id)
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
                .where(InvestigationRunRecord.run_id == run_id)
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

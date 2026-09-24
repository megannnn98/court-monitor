"""The candidates the bot shows: the rows of `/ui/candidates`, for a period.

The bot does not define a candidate of its own. It calls the selection behind the web
page with the page's defaults and the same Rosfinmonitoring snapshot the page opens on
(the newest), so a person is a candidate in the bot exactly when the page lists them.
The period is the page's «Новости с» with the bot's «по» added: both are news days in
Moscow time, as on the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session, sessionmaker

from web.candidate_rows import CandidateRow, latest_snapshot_id, select_candidate_rows


@dataclass(frozen=True)
class CandidatesResult:
    date_from: date
    date_to: date
    # None: no Rosfinmonitoring snapshot is loaded, so no absence can be confirmed.
    snapshot_id: int | None
    # In the page's order: new cases and sentences first, then by news date.
    rows: list[CandidateRow]

    @property
    def total(self) -> int:
        return len(self.rows)


class CandidatesRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def candidates(self, *, date_from: date, date_to: date) -> CandidatesResult:
        with self._session_factory() as session:
            snapshot_id = latest_snapshot_id(session)
            rows = (
                []
                if snapshot_id is None
                else select_candidate_rows(
                    session,
                    snapshot_id=snapshot_id,
                    period_start=date_from,
                    period_end=date_to,
                )
            )
        return CandidatesResult(
            date_from=date_from, date_to=date_to, snapshot_id=snapshot_id, rows=rows
        )

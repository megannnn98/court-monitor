"""Data transfer objects for sud_delo case search."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SudrfCaseSearchCriteria:
    """Criteria for searching cases on sudrf.ru via HTTP form search.

    Field semantics — see ``.cache/sudrf-live-discovery-2026-08-12.md`` for the
    live-verified evidence:

    * ``result_date`` — ``U1_CASE__RESULT_DATE1D/2D``. Date the case was
      decided against a defendant. Press releases describing a verdict
      ("вынесен приговор 02.04.2026") must use this field.
    * ``event_date``  — ``U1_EVENT__EVENT_DATEDD``. Date of *any* event in
      the case timeline (registration, hearing, preliminary...). Using it
      as a verdict date produces wildly over-broad results.
    * ``publication_date_hint`` — the press release publication date, used
      as a last-resort ordering hint. Never presented as the confirmed
      decision date; callers keep it separate from ``result_date``.
    """

    court: str
    article: str | None = None
    result_date: date | None = None
    event_date: date | None = None
    publication_date_hint: date | None = None
    case_number: str | None = None
    person_name: str | None = None
    limit: int = 20
    delo_id: str | None = None
    new_flag: str | None = None


@dataclass(frozen=True)
class SudrfCaseSearchResult:
    """Result from sud_delo search — a link to a case card."""

    case_number: str | None
    case_id: str | None
    case_uid: str | None
    delo_id: str | None
    srv_num: str | None
    url: str

    @property
    def canonical_url(self) -> str:
        return self.url


@dataclass(frozen=True)
class SearchAttempt:
    """One search attempt within a progressive search strategy.

    Logged to make the strategy auditable: which criteria were sent, how many
    results came back, what strategy name was used. Deduplication across
    attempts happens on ``case_uid`` (fallback: ``court + case_number``).
    """

    strategy: str
    criteria: SudrfCaseSearchCriteria
    result_count: int
    error: str | None = None

"""Data transfer objects for sud_delo case search."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SudrfCaseSearchCriteria:
    """Criteria for searching cases on sudrf.ru via real HTTP form search."""

    court: str
    article: str | None = None
    decision_date: date | None = None
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

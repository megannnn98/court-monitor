"""Data transfer objects for sud_delo case search."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SudrfCaseSearchCriteria:
    """Criteria for searching cases on sudrf.ru.

    This is for REMOTE search on the court website, not local DB search.
    """

    court: str  # Court identifier (e.g., "2zovs")
    article: str | None = None
    decision_date: date | None = None
    case_number: str | None = None
    person_name: str | None = None
    limit: int = 20


@dataclass(frozen=True)
class SudrfCaseSearchResult:
    """Result from sud_delo search - a link to a case card."""

    case_number: str | None
    case_id: str | None
    case_uid: str | None
    delo_id: str | None
    srv_num: str | None
    url: str  # Full URL to case card

    @property
    def canonical_url(self) -> str:
        """Return canonical URL for deduplication."""
        return self.url

"""Person ↔ Rosfinmonitoring matching service."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RosfinMatchStatus(StrEnum):
    MATCHED = "matched"
    NOT_MATCHED = "not_matched"
    AMBIGUOUS = "ambiguous"
    NEEDS_REVIEW = "needs_review"
    # The person's own name data is too thin (e.g. a single word) to reliably
    # search a snapshot at all — finding zero candidates says nothing about
    # whether they're actually absent, so this must not be reported as
    # NOT_MATCHED.
    INSUFFICIENT_DATA = "insufficient_data"


class RosfinCandidateEntry(BaseModel):
    """Candidate Rosfinmonitoring entry for a match."""

    entry_id: int
    full_name: str
    normalized_name: str
    matching_key: str
    birth_date: datetime | None = None
    similarity_score: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class RosfinMatchResult(BaseModel):
    """Result of matching a Person against Rosfinmonitoring entries."""

    model_config = ConfigDict(use_enum_values=False)

    person_id: int
    snapshot_id: int
    status: RosfinMatchStatus
    confidence: float = Field(ge=0.0, le=1.0)
    matched_entry_id: int | None = None
    matched_entry_name: str | None = None
    candidate_entries: list[RosfinCandidateEntry] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    matched_at: datetime | None = None


class RosfinmonitoringMatcher:
    """Protocol for matching persons against Rosfinmonitoring entries."""

    def match_person(
        self,
        person_id: int,
        snapshot_id: int,
    ) -> RosfinMatchResult:
        """Match a person against Rosfinmonitoring entries in a snapshot."""
        raise NotImplementedError

    def match_all_persons(
        self,
        snapshot_id: int,
    ) -> list[RosfinMatchResult]:
        """Match all persons against Rosfinmonitoring entries in a snapshot."""
        raise NotImplementedError

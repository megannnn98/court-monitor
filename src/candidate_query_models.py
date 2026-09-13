"""Main product query: politically persecuted persons absent from Rosfinmonitoring."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class RosfinmonitoringStatus(StrEnum):
    """Status of a person relative to a Rosfinmonitoring snapshot.

    Mirrors `RosfinMatchStatus` (the matcher's own status) plus
    `NO_MATCH_RECORD` for "matching was never run for this person against
    this snapshot" — that is NOT the same as a confirmed absence and must
    never be treated as one.
    """

    NOT_MATCHED = "not_matched"
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    NEEDS_REVIEW = "needs_review"
    INSUFFICIENT_DATA = "insufficient_data"
    NO_MATCH_RECORD = "no_match_record"


class PoliticalPersecutionCandidate(BaseModel):
    """A person who is politically persecuted but not in Rosfinmonitoring list."""

    person_id: int
    canonical_name: str
    normalized_name: str
    persecution_status: str
    persecution_confidence: float
    persecution_reasons: list[str] = Field(default_factory=list)
    rosfinmonitoring_status: RosfinmonitoringStatus
    rosfinmonitoring_match_confidence: float | None = None
    event_count: int = 0
    alias_count: int = 0
    last_event_date: datetime | None = None


class CandidateQueryResult(BaseModel):
    """Result of the main product query."""

    snapshot_id: int
    candidates: list[PoliticalPersecutionCandidate] = Field(default_factory=list)
    total_count: int = 0
    query_timestamp: datetime = Field(default_factory=datetime.now)

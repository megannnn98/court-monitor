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


# Product definition of "politically persecuted": a POLITICAL classification
# at or above this confidence. Shared by the candidate query and the research
# layer so both agree on who counts.
DEFAULT_MIN_PERSECUTION_CONFIDENCE = 0.7

_MATCH_RECORD_STATUS_TO_RF_STATUS: dict[str, RosfinmonitoringStatus] = {
    "matched": RosfinmonitoringStatus.MATCHED,
    "not_matched": RosfinmonitoringStatus.NOT_MATCHED,
    "ambiguous": RosfinmonitoringStatus.AMBIGUOUS,
    "needs_review": RosfinmonitoringStatus.NEEDS_REVIEW,
    "insufficient_data": RosfinmonitoringStatus.INSUFFICIENT_DATA,
}


def resolve_rosfinmonitoring_status(match_record_status: str | None) -> RosfinmonitoringStatus:
    """Map a stored `rosfin_matches.status` (or its absence) to a person-level status.

    No match record means matching was never run for this person against the
    snapshot — NO_MATCH_RECORD, not a confirmed absence. An unknown stored
    status falls back to NEEDS_REVIEW rather than being trusted.
    """
    if match_record_status is None:
        return RosfinmonitoringStatus.NO_MATCH_RECORD
    return _MATCH_RECORD_STATUS_TO_RF_STATUS.get(
        match_record_status,
        RosfinmonitoringStatus.NEEDS_REVIEW,
    )


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

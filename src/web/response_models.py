"""Pydantic response models of the REST API."""

from pydantic import BaseModel


# Pydantic models for API responses
class PersonResponse(BaseModel):
    """Person response model."""

    id: int
    canonical_name: str
    normalized_name: str
    matching_key: str
    status: str
    merged_into_id: int | None = None


class PersonAliasResponse(BaseModel):
    """Person alias response model."""

    id: int
    person_id: int
    surface_text: str
    normalized_text: str
    matching_key: str
    origin: str
    confidence: float


class PersecutionClassificationResponse(BaseModel):
    """Persecution classification response model."""

    id: int
    person_id: int
    status: str
    confidence: float
    reasons: list[str]
    evidence_types: list[str]
    classifier_name: str
    classifier_version: str


class CandidateResponse(BaseModel):
    """Candidate response model."""

    person_id: int
    canonical_name: str
    normalized_name: str
    persecution_status: str
    persecution_confidence: float
    persecution_reasons: list[str]
    rosfinmonitoring_status: str
    rosfinmonitoring_match_confidence: float | None = None
    event_count: int = 0
    alias_count: int = 0


class ArticleResponse(BaseModel):
    id: int
    source_name: str
    external_id: str
    title: str
    published_at: str | None
    url: str
    text: str


class EvidenceSpanResponse(BaseModel):
    article_id: int
    source_name: str
    title: str
    url: str
    start_offset: int
    end_offset: int
    text: str


class PersonEventResponse(BaseModel):
    id: int
    event_type: str
    event_date: str | None
    role: str
    confidence: float
    evidence: EvidenceSpanResponse


class LatestRosfinMatchResponse(BaseModel):
    snapshot_id: int
    status: str
    confidence: float
    matched_entry_id: int | None
    matched_entry_name: str | None
    reasons: list[str]


class PersonDetailResponse(BaseModel):
    person: PersonResponse
    aliases: list[PersonAliasResponse]
    persecution: PersecutionClassificationResponse | None
    rosfinmonitoring: LatestRosfinMatchResponse | None
    events: list[PersonEventResponse]


class RosfinmonitoringSnapshotResponse(BaseModel):
    """Rosfinmonitoring snapshot response model."""

    id: int
    snapshot_date: str
    source_url: str
    content_hash: str
    entry_count: int


class RosfinmonitoringEntryResponse(BaseModel):
    """Rosfinmonitoring entry response model."""

    id: int
    snapshot_id: int
    full_name: str
    normalized_name: str
    matching_key: str
    birth_date: str | None = None
    inclusion_reason: str | None = None


class ReviewResponse(BaseModel):
    """Review response model."""

    id: int
    subject_type: str
    subject_id: int
    decision: str | None = None
    confidence: float | None = None
    reason: str | None = None
    reviewer_note: str | None = None
    created_at: str
    reviewed_at: str | None = None


class OperationRunResponse(BaseModel):
    id: int
    operation: str
    title: str
    parameters: dict[str, object]
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    duration_seconds: float | None
    command: list[str]
    return_code: int | None
    stdout: str
    stderr: str
    error: str | None

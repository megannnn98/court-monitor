"""Structured research requests and their normalized results.

The research layer answers deterministic questions about research objects
(currently only Person). Articles are not results: they appear only as
evidence/provenance for facts about a person.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from candidates.models import DEFAULT_MIN_PERSECUTION_CONFIDENCE, RosfinmonitoringStatus
from extraction.models import EventEntityRole, EventType
from persecution.models import PersecutionClassification, PersecutionClassificationStatus
from persons.models import Person, PersonAlias
from rosfinmonitoring.matcher_models import RosfinCandidateEntry

MAX_RESEARCH_LIMIT = 1000
DEFAULT_RESEARCH_LIMIT = 20
MAX_SEMANTIC_QUERY_CHARS = 500


class ResearchObjectType(StrEnum):
    """Kind of research object a request asks for.

    Only PERSON is implemented. EVENT/CASE/ORGANIZATION are intentionally not
    declared until the data model can answer them (see ADR 0008).
    """

    PERSON = "person"


class PersonResearchCriteria(BaseModel):
    """Deterministic filters over canonical persons. All filters are AND-ed.

    Only criteria the current data model can execute exactly are accepted;
    unknown fields are rejected instead of being silently ignored.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    person_id: int | None = Field(default=None, gt=0)
    # Case-insensitive substring over canonical/normalized name and aliases.
    name: str | None = None
    # Latest classification of the person (by classified_at).
    persecution_status: PersecutionClassificationStatus | None = None
    persecution_min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    # Status relative to `snapshot_id`.
    rosfinmonitoring_status: RosfinmonitoringStatus | None = None
    # Also selects which snapshot the result's Rosfinmonitoring section refers to.
    snapshot_id: int | None = Field(default=None, gt=0)
    # Person has at least one linked event of one of these types (and within
    # the date range, if given — the same event must satisfy both).
    event_types: list[EventType] | None = Field(default=None, min_length=1)
    # Inclusive UTC calendar-day bounds on linked event dates.
    date_from: date | None = None
    date_to: date | None = None
    # `sources.name` (e.g. "ОВД-Инфо"): person has a mention or linked event
    # extracted from an article of this source.
    source: str | None = None
    # Free-text description of circumstances/topic ("антивоенные публикации").
    # Not a filter SQL can evaluate: it selects a candidate pool by semantic
    # retrieval (ADR 0011), and every other criterion is still applied exactly.
    semantic_query: str | None = Field(default=None, max_length=MAX_SEMANTIC_QUERY_CHARS)

    @field_validator("name", "source", "semantic_query")
    @classmethod
    def validate_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from > self.date_to
        ):
            raise ValueError("date_from must not be after date_to")
        if self.rosfinmonitoring_status is not None and self.snapshot_id is None:
            # Match statuses only exist relative to a specific snapshot.
            raise ValueError("rosfinmonitoring_status requires snapshot_id")
        if self.persecution_min_confidence is not None and self.persecution_status is None:
            raise ValueError("persecution_min_confidence requires persecution_status")
        return self

    @property
    def effective_persecution_min_confidence(self) -> float | None:
        """Threshold actually applied to the persecution filter.

        POLITICAL without an explicit threshold uses the candidate query's
        product definition, so "political" means the same thing everywhere.
        """
        if self.persecution_min_confidence is not None:
            return self.persecution_min_confidence
        if self.persecution_status is PersecutionClassificationStatus.POLITICAL:
            return DEFAULT_MIN_PERSECUTION_CONFIDENCE
        return None


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    object_type: ResearchObjectType
    criteria: PersonResearchCriteria = Field(default_factory=PersonResearchCriteria)
    limit: int = Field(default=DEFAULT_RESEARCH_LIMIT, ge=1, le=MAX_RESEARCH_LIMIT)


class ResearchSource(BaseModel):
    """An article that provides evidence for a result, with its origin."""

    article_id: int
    article_title: str
    source_name: str
    url: str
    published_at: datetime | None = None


class ResearchEvidenceType(StrEnum):
    PERSON_MENTION = "person_mention"
    EVENT = "event"


class ResearchEvidence(BaseModel):
    """A span of an article that supports a fact about this person.

    `text` is only the span itself, never the full article; `article_id`
    points into the result's `sources`.
    """

    model_config = ConfigDict(use_enum_values=False)

    evidence_type: ResearchEvidenceType
    article_id: int
    extraction_run_id: int
    start_offset: int
    end_offset: int
    text: str
    mention_id: int | None = None
    event_id: int | None = None


class ResearchEvent(BaseModel):
    """An extracted event linked to this canonical person."""

    model_config = ConfigDict(use_enum_values=False)

    event_id: int
    event_type: EventType
    event_date: datetime | None = None
    # Roles this person plays in the event (a person may hold several).
    roles: list[EventEntityRole]
    confidence: float
    attributes: dict[str, Any] = Field(default_factory=dict)
    article_id: int


class ResearchRosfinmonitoring(BaseModel):
    """Person status relative to one Rosfinmonitoring snapshot."""

    model_config = ConfigDict(use_enum_values=False)

    snapshot_id: int
    status: RosfinmonitoringStatus
    confidence: float | None = None
    matched_entry_id: int | None = None
    matched_entry_name: str | None = None
    candidate_entries: list[RosfinCandidateEntry] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    matched_at: datetime | None = None


class ResearchWarningCode(StrEnum):
    PERSECUTION_NOT_CLASSIFIED = "persecution_not_classified"
    PERSECUTION_UNCERTAIN = "persecution_uncertain"
    PERSECUTION_NEEDS_REVIEW = "persecution_needs_review"
    ROSFIN_NO_MATCH_RECORD = "rosfin_no_match_record"
    ROSFIN_AMBIGUOUS = "rosfin_ambiguous"
    ROSFIN_NEEDS_REVIEW = "rosfin_needs_review"
    ROSFIN_INSUFFICIENT_DATA = "rosfin_insufficient_data"
    # Only the newest evidence/events of the person are included (bounded response).
    EVIDENCE_TRUNCATED = "evidence_truncated"


class ResearchWarning(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    code: ResearchWarningCode
    message: str
    # True when a human has to decide; False when the pipeline simply has not
    # produced the data yet (run the classifier/matcher instead).
    requires_review: bool


class PersonResearchResult(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    object_type: ResearchObjectType = ResearchObjectType.PERSON
    person: Person
    aliases: list[PersonAlias] = Field(default_factory=list)
    persecution: PersecutionClassification | None = None
    # None when the request did not name a snapshot.
    rosfinmonitoring: ResearchRosfinmonitoring | None = None
    events: list[ResearchEvent] = Field(default_factory=list)
    evidence: list[ResearchEvidence] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)
    warnings: list[ResearchWarning] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def review_required(self) -> bool:
        return any(warning.requires_review for warning in self.warnings)


class ResearchResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    object_type: ResearchObjectType
    request: ResearchRequest
    results: list[PersonResearchResult] = Field(default_factory=list)
    # Persons matching the criteria before `limit` was applied. A person removed
    # concurrently after filtering is still counted but absent from `results`.
    total_matched: int

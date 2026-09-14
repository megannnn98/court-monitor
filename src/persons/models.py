from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PersonStatus(StrEnum):
    ACTIVE = "active"
    MERGED = "merged"
    NEEDS_REVIEW = "needs_review"


class AliasOrigin(StrEnum):
    EXTRACTION = "extraction"
    MANUAL = "manual"
    RESOLUTION = "resolution"
    MERGE = "merge"


class MergeStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    REVERTED = "reverted"


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class Person(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    id: int | None = None
    canonical_name: str
    normalized_name: str
    matching_key: str
    status: PersonStatus = PersonStatus.ACTIVE
    merged_into_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("canonical_name", "normalized_name", "matching_key")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be empty")
        return value


class PersonAlias(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    id: int | None = None
    person_id: int
    surface_text: str
    normalized_text: str
    matching_key: str
    origin: AliasOrigin
    confidence: float = Field(ge=0.0, le=1.0)
    source_mention_id: int | None = None
    created_at: datetime | None = None


class MergeRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    id: int | None = None
    source_person_id: int
    target_person_id: int
    status: MergeStatus = MergeStatus.PENDING
    reason: str | None = None
    applied_at: datetime | None = None
    created_at: datetime | None = None


class ReviewRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    id: int | None = None
    subject_type: str
    subject_id: int
    decision: ReviewDecision | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str | None = None
    reviewer_note: str | None = None
    created_at: datetime | None = None
    reviewed_at: datetime | None = None

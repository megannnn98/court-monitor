"""Workflow-level models: intake output and the assembled query result."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from research_models import PersonResearchResult, ResearchRequest


class UnsupportedCriterion(BaseModel):
    """A constraint from the query that ResearchRequest cannot express."""

    model_config = ConfigDict(extra="forbid")

    # Short name of the dimension, e.g. "occupation", "age", "region".
    criterion: str = Field(min_length=1)
    # The user's wording, e.g. "программисты", "30–35 лет", "из Казани".
    value: str = Field(min_length=1)


class ResearchIntake(BaseModel):
    """What request intake extracted from the query.

    `request` is the raw structured candidate. It is NOT a validated
    `ResearchRequest` yet: snapshot resolution and domain validation happen
    in later workflow nodes.
    """

    model_config = ConfigDict(extra="forbid")

    request: dict[str, Any] | None = None
    unsupported_criteria: list[UnsupportedCriterion] = Field(default_factory=list)
    # Set only when the query cannot be mapped to a request without asking.
    clarification_question: str | None = None


class RosfinmonitoringSnapshotSummary(BaseModel):
    snapshot_id: int
    snapshot_date: datetime
    entry_count: int
    match_count: int


class WorkflowStatus(StrEnum):
    COMPLETED = "completed"
    CLARIFICATION_REQUIRED = "clarification_required"
    FAILED = "failed"


class WorkflowErrorCode(StrEnum):
    LLM_NOT_CONFIGURED = "llm_not_configured"
    LLM_TIMEOUT = "llm_timeout"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_AUTHENTICATION_FAILED = "llm_authentication_failed"
    LLM_RATE_LIMITED = "llm_rate_limited"
    LLM_REQUEST_REJECTED = "llm_request_rejected"
    LLM_INVALID_OUTPUT = "llm_invalid_output"
    NO_ROSFINMONITORING_SNAPSHOT = "no_rosfinmonitoring_snapshot"


class WorkflowError(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    code: WorkflowErrorCode
    message: str


class ResearchQueryResult(BaseModel):
    """Deterministically assembled workflow result.

    `results` are the `ResearchService` results unchanged: statuses,
    evidence and per-person warnings are never rewritten here.
    """

    model_config = ConfigDict(use_enum_values=False)

    status: WorkflowStatus
    query: str
    # The validated request actually executed (or the last valid one).
    request: ResearchRequest | None = None
    results: list[PersonResearchResult] = Field(default_factory=list)
    total_matched: int | None = None
    unsupported_criteria: list[UnsupportedCriterion] = Field(default_factory=list)
    # Workflow-level notes (e.g. snapshot defaulting). Per-person warnings
    # stay inside each result.
    warnings: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    error: WorkflowError | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def clarification_required(self) -> bool:
        return self.status is WorkflowStatus.CLARIFICATION_REQUIRED

    @computed_field  # type: ignore[prop-decorator]
    @property
    def review_required(self) -> bool:
        return any(result.review_required for result in self.results)

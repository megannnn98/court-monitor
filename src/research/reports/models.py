"""Report-level models: claims, citations, review decisions, report status.

These models present facts from `PersonResearchResult`; they never replace
it. Statuses and confidences are copied, not recomputed.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from candidates.models import RosfinmonitoringStatus
from persecution.models import PersecutionClassificationStatus
from research.models import (
    ResearchEvent,
    ResearchEvidenceType,
    ResearchRequest,
    ResearchWarning,
)
from research.planning.models import ResearchRetrievalMode, SourceRoutingDecision
from semantic_retrieval.models import RetrievalBackend

# --- human review -------------------------------------------------------------------


# Bump when report claims, citations or statuses change meaning.
# 2: NON_POLITICAL is worded as "no evidence found", never as an established fact.
RESEARCH_REPORT_VERSION = "2"


class ResearchReviewReason(StrEnum):
    """Why a result needs a human decision.

    Only reasons the current data model can actually produce. Entity
    resolution ambiguity is not listed: research returns active canonical
    persons only, so it is not visible in a result.
    """

    PERSECUTION_UNCERTAIN = "persecution_uncertain"
    PERSECUTION_NEEDS_REVIEW = "persecution_needs_review"
    ROSFIN_AMBIGUOUS = "rosfin_ambiguous"
    ROSFIN_NEEDS_REVIEW = "rosfin_needs_review"
    ROSFIN_INSUFFICIENT_DATA = "rosfin_insufficient_data"
    # A source-derived fact (classification, event) has no citable span.
    MISSING_EVIDENCE = "missing_evidence"
    # POLITICAL below the product threshold (DEFAULT_MIN_PERSECUTION_CONFIDENCE).
    LOW_CONFIDENCE = "low_confidence"


class ResearchReviewSeverity(StrEnum):
    NONE = "none"
    # The fact stands, but a human should double-check it.
    ADVISORY = "advisory"
    # The fact is not established until a human decides.
    BLOCKING = "blocking"


_SEVERITY_ORDER = {
    ResearchReviewSeverity.NONE: 0,
    ResearchReviewSeverity.ADVISORY: 1,
    ResearchReviewSeverity.BLOCKING: 2,
}


class ResearchReviewReasonDetail(BaseModel):
    """One review reason with references to the stored record it concerns.

    The references are what `POST /research/reviews` needs to create a
    persistent review task; `message` is fixed text, never LLM output.
    """

    model_config = ConfigDict(use_enum_values=False)

    code: ResearchReviewReason
    severity: ResearchReviewSeverity
    message: str
    classification_id: int | None = None
    snapshot_id: int | None = None
    event_id: int | None = None


class ResearchReviewDecision(BaseModel):
    """Whether a result requires review. Not whether a review record exists."""

    model_config = ConfigDict(use_enum_values=False)

    reasons: list[ResearchReviewReasonDetail] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def required(self) -> bool:
        return bool(self.reasons)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def severity(self) -> ResearchReviewSeverity:
        return max(
            (reason.severity for reason in self.reasons),
            key=_SEVERITY_ORDER.__getitem__,
            default=ResearchReviewSeverity.NONE,
        )


# --- claims and citations -----------------------------------------------------------


class ResearchCitation(BaseModel):
    """A stored evidence span together with the article it comes from.

    Built from `ResearchEvidence` + `ResearchSource`; `text` is the span only.
    """

    model_config = ConfigDict(use_enum_values=False)

    article_id: int
    article_title: str
    source_name: str
    url: str
    published_at: datetime | None = None
    evidence_type: ResearchEvidenceType
    text: str
    start_offset: int
    end_offset: int
    extraction_run_id: int
    mention_id: int | None = None
    event_id: int | None = None


class ResearchClaimType(StrEnum):
    IDENTITY = "identity"
    PERSECUTION_CLASSIFICATION = "persecution_classification"
    ROSFINMONITORING_STATUS = "rosfinmonitoring_status"
    EVENT = "event"


class ResearchClaimBasis(StrEnum):
    # Derived from article text: must be backed by citations.
    SOURCE_DOCUMENTS = "source_documents"
    # A match result against a Rosfinmonitoring snapshot: provenance is the
    # snapshot id, there is no article to cite.
    ROSFINMONITORING_SNAPSHOT = "rosfinmonitoring_snapshot"


class ResearchClaim(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    claim_type: ResearchClaimType
    basis: ResearchClaimBasis
    text: str
    # The confidence of this specific fact (classification, match, event
    # extraction); never an aggregate.
    confidence: float | None = None
    citations: list[ResearchCitation] = Field(default_factory=list)
    review_required: bool = False
    classification_id: int | None = None
    snapshot_id: int | None = None
    event_id: int | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def supported(self) -> bool:
        """False for a source-derived claim without any citation."""
        return self.basis is not ResearchClaimBasis.SOURCE_DOCUMENTS or bool(self.citations)


class ResearchMatchReason(BaseModel):
    """One criterion that was actually applied, and this person's value for it."""

    criterion: str
    requested: str
    actual: str


class ResearchReportWarningCode(StrEnum):
    # Source-derived claim without citation (also a MISSING_EVIDENCE review).
    CLAIM_WITHOUT_CITATION = "claim_without_citation"
    # Evidence points to an article missing from the result's sources.
    EVIDENCE_WITHOUT_SOURCE = "evidence_without_source"
    # Data the report cannot state yet (not a confirmed negative).
    PERSECUTION_NOT_CLASSIFIED = "persecution_not_classified"
    ROSFINMONITORING_NOT_MATCHED_YET = "rosfinmonitoring_not_matched_yet"
    # More persons matched than the request limit allowed to show.
    RESULTS_TRUNCATED = "results_truncated"
    # Results come from a bounded semantic candidate pool (ADR 0011).
    SEMANTIC_CANDIDATE_POOL = "semantic_candidate_pool"


class ResearchReportWarning(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    code: ResearchReportWarningCode
    message: str


# --- report -------------------------------------------------------------------------


class ResearchReportItem(BaseModel):
    """One person: facts copied from `PersonResearchResult`, plus presentation."""

    model_config = ConfigDict(use_enum_values=False)

    person_id: int
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)

    persecution_status: PersecutionClassificationStatus | None = None
    persecution_confidence: float | None = None
    persecution_reasons: list[str] = Field(default_factory=list)
    classification_id: int | None = None

    snapshot_id: int | None = None
    rosfinmonitoring_status: RosfinmonitoringStatus | None = None
    rosfinmonitoring_confidence: float | None = None
    # Fixed wording for the status; NO_MATCH_RECORD/INSUFFICIENT_DATA never
    # read as absence.
    rosfinmonitoring_summary: str | None = None

    events: list[ResearchEvent] = Field(default_factory=list)
    # Position among semantic candidates (1 = most similar). Diagnostic only:
    # never a confidence and never a reason to believe any fact.
    retrieval_rank: int | None = None
    why_matched: list[ResearchMatchReason] = Field(default_factory=list)
    claims: list[ResearchClaim] = Field(default_factory=list)
    citations: list[ResearchCitation] = Field(default_factory=list)
    # Warnings produced by ResearchService, unchanged.
    domain_warnings: list[ResearchWarning] = Field(default_factory=list)
    report_warnings: list[ResearchReportWarning] = Field(default_factory=list)
    review: ResearchReviewDecision = Field(default_factory=ResearchReviewDecision)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def review_required(self) -> bool:
        return self.review.required

    @computed_field  # type: ignore[prop-decorator]
    @property
    def partial(self) -> bool:
        """Some fact could not be stated (data gap), without needing review."""
        return bool(self.report_warnings)


class ResearchReportStatus(StrEnum):
    """Status of the report itself, not of any person."""

    # Every shown person has all requested facts, cited, no review needed.
    COMPLETE = "complete"
    # Results exist, but some facts are not available yet (data gaps).
    PARTIAL = "partial"
    # At least one result needs a human decision.
    REVIEW_REQUIRED = "review_required"
    # Nothing matched and a source refresh would not change that.
    NO_MATCHES = "no_matches"
    # Nothing matched in the current database; it may be incomplete and a
    # source refresh is recommended.
    INSUFFICIENT_DATA = "insufficient_data"


class ResearchRetrievalMetadata(BaseModel):
    """How the population was selected. Diagnostics, not facts; no scores."""

    model_config = ConfigDict(use_enum_values=False)

    mode: ResearchRetrievalMode
    backend: RetrievalBackend | None = None
    candidate_pool_size: int = 0
    # Nearest neighbours found by retrieval (ranking only).
    candidates_returned: int = 0
    # Candidates similar enough to satisfy semantic_query; only these were
    # given to ResearchService.
    candidates_accepted: int = 0
    # Minimum dense cosine similarity used for acceptance.
    min_similarity: float | None = None


class ResearchReportSummary(BaseModel):
    total_matched: int
    returned: int
    review_required_count: int
    partial_count: int
    # Human-readable status explanation (fixed wording).
    text: str


class ResearchReport(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    status: ResearchReportStatus
    request: ResearchRequest
    summary: ResearchReportSummary
    items: list[ResearchReportItem] = Field(default_factory=list)
    warnings: list[ResearchReportWarning] = Field(default_factory=list)
    source_routing: SourceRoutingDecision
    retrieval: ResearchRetrievalMetadata = Field(
        default_factory=lambda: ResearchRetrievalMetadata(mode=ResearchRetrievalMode.STRUCTURED)
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def review_required(self) -> bool:
        return any(item.review_required for item in self.items)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_refresh_recommended(self) -> bool:
        return self.source_routing.source_refresh_required

    @computed_field  # type: ignore[prop-decorator]
    @property
    def recommended_sources(self) -> list[str]:
        return list(self.source_routing.sources)

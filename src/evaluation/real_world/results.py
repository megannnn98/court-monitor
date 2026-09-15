"""Typed, stable schema of `reports/real_world_validation_v1.json`."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from evaluation.real_world.golden import DangerousKind

Metric = float | int | None


class SectionStatus(StrEnum):
    RUN = "RUN"
    PARTIAL = "PARTIAL"
    # Not executed (model, service, credentials or data missing): never a PASS.
    NOT_RUN = "NOT_RUN"


class ErrorComponent(StrEnum):
    EXTRACTION = "EXTRACTION"
    EVENT_ASSOCIATION = "EVENT_ASSOCIATION"
    ENTITY_RESOLUTION = "ENTITY_RESOLUTION"
    PERSECUTION = "PERSECUTION"
    ROSFINMONITORING = "ROSFINMONITORING"
    RETRIEVAL = "RETRIEVAL"
    RESEARCH_INTAKE = "RESEARCH_INTAKE"
    REPORT = "REPORT"
    MONITORING = "MONITORING"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    DATA_QUALITY = "DATA_QUALITY"


class Severity(StrEnum):
    S0 = "S0"  # safety-critical: a false statement about a real person
    S1 = "S1"  # wrong final result
    S2 = "S2"  # recall / review issue
    S3 = "S3"  # performance / cosmetic


class GateKind(StrEnum):
    HARD = "hard"
    QUALITY = "quality"
    ADVISORY = "advisory"


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"


class OverallStatus(StrEnum):
    PASSED = "PASSED"
    FAILED_GATES = "FAILED_GATES"
    # Too few VERIFIED golden cases (or DRAFT annotations): no production claim.
    PRELIMINARY = "PRELIMINARY"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Failure(_Model):
    component: ErrorComponent
    severity: Severity
    kind: str
    detail: str
    case_id: str | None = None
    golden_person_id: str | None = None
    # `source:external_id` of the article that shows the problem.
    article: str | None = None
    evidence: str | None = None
    dangerous_kind: DangerousKind | None = None
    # False only when the case's known_limitation_kinds lists this dangerous kind.
    gated: bool = True


class GateOutcome(_Model):
    name: str
    kind: GateKind
    status: GateStatus
    value: Metric
    threshold: float
    comparator: str
    detail: str | None = None


class Provenance(_Model):
    git_commit: str
    evaluation_version: str
    dataset_version: str
    corpus_manifest_hash: str | None
    golden_dataset_hash: str
    rf_snapshot_id: str
    rf_snapshot_hash: str | None
    embedding_model_id: str | None
    extractor_version: str
    classifier_version: str
    matcher_version: str
    resolver_version: str
    component_versions: dict[str, str]
    policy_version: str
    policy_hash: str
    thresholds: dict[str, dict[str, float]]
    split: str
    verified_only: bool
    scenarios: list[str]
    timestamp: datetime


class SplitCounts(_Model):
    draft: int = 0
    verified: int = 0


class DatasetSummary(_Model):
    corpus_articles: int
    corpus_by_source: dict[str, int]
    corpus_by_period: dict[str, int]
    corpus_period: str | None
    corpus_published_range: str | None
    evaluation_sample: int
    source_status: dict[str, str]
    golden_articles: int
    golden_draft: int
    golden_verified: int
    golden_persons: int
    golden_by_split: dict[str, SplitCounts]
    evaluated_articles: int
    evaluated_persons: int
    namesake_cases: int
    retrieval_queries: int
    research_queries: int


class ExtractionSection(_Model):
    status: SectionStatus
    person_mentions: dict[str, Metric] = Field(default_factory=dict)
    events: dict[str, Metric] = Field(default_factory=dict)
    historical_events: dict[str, Metric] = Field(default_factory=dict)
    events_matched: int = 0
    events_association_correct: int = 0
    person_event_association_accuracy: float | None = None
    cross_person_event_links: int = 0


class NamesakeSection(_Model):
    status: SectionStatus
    not_run_reason: str | None = None
    cases: int = 0
    by_category: dict[str, int] = Field(default_factory=dict)
    different_person_auto_links: int = 0
    decision: dict[str, Metric] = Field(default_factory=dict)
    candidate_recall_at: dict[str, float] = Field(default_factory=dict)


class EntityResolutionSection(_Model):
    status: SectionStatus
    mentions_evaluated: int = 0
    link_expected_mentions: int = 0
    candidate_recall_at_1: float | None = None
    candidate_recall_at_5: float | None = None
    auto_links: int = 0
    correct_auto_links: int = 0
    auto_link_precision: float | None = None
    auto_link_recall: float | None = None
    reviews: int = 0
    review_rate: float | None = None
    false_links: int = 0
    false_create_new: int = 0
    duplicate_canonical_persons: int = 0
    unresolved_mentions: int = 0
    namesake: NamesakeSection = Field(
        default_factory=lambda: NamesakeSection(status=SectionStatus.NOT_RUN)
    )


class PersecutionSection(_Model):
    status: SectionStatus
    evaluated: int = 0
    unmapped_persons: int = 0
    accuracy: float | None = None
    political: dict[str, Metric] = Field(default_factory=dict)
    non_political: dict[str, Metric] = Field(default_factory=dict)
    uncertain_or_review_rate: float | None = None
    confusion_matrix: dict[str, dict[str, int]] = Field(default_factory=dict)
    cross_person_political_attribution: int = 0


class RosfinSection(_Model):
    status: SectionStatus
    snapshot_id: str | None = None
    evaluated: int = 0
    accuracy: float | None = None
    confusion_matrix: dict[str, dict[str, int]] = Field(default_factory=dict)
    false_not_matched: int = 0
    review_instead_of_expected: int = 0


class EvidenceSection(_Model):
    candidates_checked: int = 0
    with_evidence_rate: float | None = None
    relevant_evidence_rate: float | None = None
    supports_classification_rate: float | None = None
    traceable_rate: float | None = None
    offset_valid_rate: float | None = None
    evidence_spans: int = 0


class CandidateSection(_Model):
    status: SectionStatus
    snapshot_id: str | None = None
    candidates: dict[str, Metric] = Field(default_factory=dict)
    findings: dict[str, Metric] = Field(default_factory=dict)
    evidence: EvidenceSection = Field(default_factory=EvidenceSection)


class BackendRetrieval(_Model):
    cases: int
    recall_at_5: float
    recall_at_10: float
    mrr: float
    ndcg_at_5: float


class RetrievalSection(_Model):
    status: SectionStatus
    not_run_reason: str | None = None
    embedding_model_id: str | None = None
    queries: int = 0
    person_queries: int = 0
    event_queries: int = 0
    backends: dict[str, BackendRetrieval] = Field(default_factory=dict)
    semantic_recall_at_5: float | None = None


class ClaimSupport(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    # The claim is about a person or article the golden dataset does not cover.
    NOT_EVALUATED = "NOT_EVALUATED"


class ResearchSection(_Model):
    status: SectionStatus
    queries: int = 0
    by_class: dict[str, int] = Field(default_factory=dict)
    intake_status: SectionStatus = SectionStatus.NOT_RUN
    intake_not_run_reason: str | None = None
    intake_correct: int = 0
    intake_clarification_expected: int = 0
    intake_clarification_given: int = 0
    dangerous_silent_reinterpretation: int = 0
    workflow_completed: int = 0
    workflow_clarification: int = 0
    workflow_failed: int = 0
    persons: dict[str, Metric] = Field(default_factory=dict)
    claims: dict[str, int] = Field(default_factory=dict)
    supported_claims_rate: float | None = None
    contradicted_claims: int = 0
    # Distinct (person, claim type, value) behind the contradicted claim occurrences.
    contradicted_claims_unique: int = 0
    # SUPPORT:category -> count (occurrences / distinct person facts); see claim_failure_category.
    claim_failure_categories: dict[str, int] = Field(default_factory=dict)
    claim_failure_categories_unique: dict[str, int] = Field(default_factory=dict)
    unsupported_rf_absence_claims: int = 0
    required_claims_missing: int = 0
    forbidden_claims_present: int = 0
    dangerous: dict[str, int] = Field(default_factory=dict)


class ScenarioResult(_Model):
    name: str
    status: GateStatus
    detail: str
    metrics: dict[str, Metric | str] = Field(default_factory=dict)


class PeriodResult(_Model):
    period: str
    articles_published: int
    run_status: dict[str, str]
    new: dict[str, int]
    rerun_new: dict[str, int]
    duration_seconds: float


class MonitoringSection(_Model):
    status: SectionStatus
    not_run_reason: str | None = None
    articles: int = 0
    periods: list[PeriodResult] = Field(default_factory=list)
    rerun_duplicates: dict[str, int] = Field(default_factory=dict)
    finding_timing: dict[str, int] = Field(default_factory=dict)
    scenarios: list[ScenarioResult] = Field(default_factory=list)
    review_workload: dict[str, Metric] = Field(default_factory=dict)
    db_invariants: dict[str, int] = Field(default_factory=dict)


class PerformanceSection(_Model):
    status: SectionStatus
    articles: int = 0
    persons: int = 0
    total_seconds: float | None = None
    articles_per_minute: float | None = None
    persons_per_minute: float | None = None
    stage_seconds: dict[str, float] = Field(default_factory=dict)


class RecommendedWork(_Model):
    priority: int
    component: ErrorComponent
    problem: str
    metric: str
    value: Metric
    target: str
    evidence: str
    possible_work: str


class RealWorldValidationReport(_Model):
    evaluation_version: str
    overall_status: OverallStatus
    exit_code: int
    status_reasons: list[str]
    provenance: Provenance
    dataset: DatasetSummary
    extraction: ExtractionSection
    entity_resolution: EntityResolutionSection
    persecution: PersecutionSection
    rosfinmonitoring: RosfinSection
    candidate_query: CandidateSection
    retrieval: RetrievalSection
    research: ResearchSection
    monitoring: MonitoringSection
    performance: PerformanceSection
    safety_gates: list[GateOutcome]
    failures: list[Failure]
    failure_summary: dict[str, dict[str, int]]
    known_limitations: list[str]
    recommended_next_work: list[RecommendedWork]

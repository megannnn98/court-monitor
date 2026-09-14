"""Research plan and source routing models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SourceDataType(StrEnum):
    """What a registered source contributes to the database."""

    # Articles are parsed, then extraction/resolution/classification derive
    # persons, events and persecution classifications from them.
    ARTICLES = "articles"


class SourceCapability(BaseModel):
    """What a source from `source_registry` can do, as declared there."""

    model_config = ConfigDict(use_enum_values=False)

    # Registry key, e.g. "ovd-info" (CLI `--source`, `discover-and-ingest`).
    source_id: str
    # `sources.name` in the database, e.g. "ОВД-Инфо".
    source_name: str
    base_url: str
    supports_discovery: bool
    supports_direct_fetch: bool
    data_types: list[SourceDataType]


class ResearchRetrievalMode(StrEnum):
    """How the person population is selected before deterministic filters."""

    # Only structured PostgreSQL criteria; no vector index involved.
    STRUCTURED = "structured"
    # semantic_query: lexical + dense candidates fused with RRF (ADR 0011).
    HYBRID = "hybrid"
    # HYBRID followed by a cross-encoder reranker (SEMANTIC_RERANK=1).
    HYBRID_RERANKED = "hybrid_reranked"


class ResearchDataRequirement(StrEnum):
    """Stored data a request depends on."""

    PERSONS = "persons"
    SOURCE_MENTIONS = "source_mentions"
    EVENTS = "events"
    PERSECUTION_CLASSIFICATIONS = "persecution_classifications"
    ROSFINMONITORING_MATCHES = "rosfinmonitoring_matches"
    # semantic_documents + vector collection for persons.
    SEMANTIC_INDEX = "semantic_index"


class ResearchPlanStepType(StrEnum):
    RETRIEVE_CANDIDATES = "retrieve_candidates"
    DATABASE_SEARCH = "database_search"
    EVALUATE_RESULT = "evaluate_result"
    BUILD_REPORT = "build_report"
    HUMAN_REVIEW_GATE = "human_review_gate"


class ResearchPlanStep(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    step_type: ResearchPlanStepType
    description: str


class ResearchPlan(BaseModel):
    """What the workflow will do for a validated request.

    Database first: the database is always searched, and whether a source
    refresh is worth recommending is decided only after that search
    (`SourceRoutingDecision`). `candidate_sources` are the registry sources
    compatible with the request, not sources that will be crawled.
    """

    model_config = ConfigDict(use_enum_values=False)

    database_search: bool = True
    retrieval_mode: ResearchRetrievalMode = ResearchRetrievalMode.STRUCTURED
    # Max semantic candidates passed to ResearchService (0 for structured).
    candidate_pool_size: int = 0
    steps: list[ResearchPlanStep]
    data_requirements: list[ResearchDataRequirement]
    candidate_sources: list[SourceCapability] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SourceRoutingReason(StrEnum):
    # The database returned matching persons; nothing to refresh.
    DATABASE_SUFFICIENT = "database_sufficient"
    # Nothing matched; the local copy of the sources may be incomplete.
    NO_MATCHES_IN_DATABASE = "no_matches_in_database"
    # Nothing matched, but the request targets a known person id: re-ingesting
    # sources cannot create that id.
    REFRESH_CANNOT_HELP = "refresh_cannot_help"
    # Nothing matched, and no registered source is compatible with the request.
    NO_COMPATIBLE_SOURCE = "no_compatible_source"


class SourceRoutingDecision(BaseModel):
    """Recommendation after the database search. Never an executed action."""

    model_config = ConfigDict(use_enum_values=False)

    database_search: bool = True
    source_refresh_required: bool
    # Registry source ids, in registry order.
    sources: list[str] = Field(default_factory=list)
    reason: SourceRoutingReason

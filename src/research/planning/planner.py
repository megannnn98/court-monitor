"""Deterministic planner: which data a request needs and whether to refresh sources.

Policy (database first):
1. The database is always searched first; the plan never starts with crawling.
2. Candidate sources are registry sources compatible with the request: all of
   them, or only the one whose `source_name` equals `criteria.source` (the
   exact `sources.name` filter ResearchService applies). Anything else yields
   no candidates.
3. Retrieval routing: a request with `criteria.semantic_query` uses hybrid
   retrieval (reranked when configured) to select a bounded candidate pool;
   every other request is structured only. Exact criteria (person_id, name,
   statuses, snapshot, dates, event types, source) never go to embeddings.
4. After the search, a refresh is recommended only when nothing matched, a
   compatible source that supports discovery exists, and the request is not a lookup of a known
   person id (re-ingestion cannot create that id).
The decision is a recommendation; nothing here runs ingestion.
"""

from __future__ import annotations

from collections.abc import Mapping

from research.models import ResearchRequest, ResearchResponse
from research.planning.models import (
    ResearchDataRequirement,
    ResearchPlan,
    ResearchPlanStep,
    ResearchPlanStepType,
    ResearchRetrievalMode,
    SourceCapability,
    SourceDataType,
    SourceRoutingDecision,
    SourceRoutingReason,
)
from sources.source_registry import SourceDefinition

_RETRIEVE_STEP = ResearchPlanStep(
    step_type=ResearchPlanStepType.RETRIEVE_CANDIDATES,
    description="Semantic candidate retrieval (lexical + dense + RRF); candidates, not facts.",
)

DEFAULT_CANDIDATE_POOL_SIZE = 100

_STEPS = [
    ResearchPlanStep(
        step_type=ResearchPlanStepType.DATABASE_SEARCH,
        description="Structured search via ResearchService over the current database.",
    ),
    ResearchPlanStep(
        step_type=ResearchPlanStepType.EVALUATE_RESULT,
        description="Review policy per result and source routing decision.",
    ),
    ResearchPlanStep(
        step_type=ResearchPlanStepType.BUILD_REPORT,
        description="Deterministic report with claims and citations.",
    ),
    ResearchPlanStep(
        step_type=ResearchPlanStepType.HUMAN_REVIEW_GATE,
        description="Mark review-required results; no review record is created.",
    ),
]


def source_capabilities(sources: Mapping[str, SourceDefinition]) -> list[SourceCapability]:
    return [
        SourceCapability(
            source_id=source_id,
            source_name=definition.source_name,
            base_url=definition.base_url,
            supports_discovery=definition.supports_discovery,
            supports_direct_fetch=definition.supports_direct_fetch,
            data_types=[SourceDataType.ARTICLES],
        )
        for source_id, definition in sources.items()
    ]


class ResearchPlanner:
    def __init__(
        self,
        sources: Mapping[str, SourceDefinition],
        *,
        rerank_semantic: bool = False,
        candidate_pool_size: int = DEFAULT_CANDIDATE_POOL_SIZE,
    ) -> None:
        if candidate_pool_size <= 0:
            raise ValueError("candidate_pool_size must be greater than 0")
        self._capabilities = source_capabilities(sources)
        self._rerank_semantic = rerank_semantic
        self._candidate_pool_size = candidate_pool_size

    def plan(self, request: ResearchRequest) -> ResearchPlan:
        criteria = request.criteria
        requirements = [ResearchDataRequirement.PERSONS]
        if criteria.source is not None:
            requirements.append(ResearchDataRequirement.SOURCE_MENTIONS)
        if criteria.event_types is not None or criteria.date_from or criteria.date_to:
            requirements.append(ResearchDataRequirement.EVENTS)
        if criteria.persecution_status is not None:
            requirements.append(ResearchDataRequirement.PERSECUTION_CLASSIFICATIONS)
        if criteria.snapshot_id is not None:
            requirements.append(ResearchDataRequirement.ROSFINMONITORING_MATCHES)
        semantic = criteria.semantic_query is not None
        if semantic:
            requirements.append(ResearchDataRequirement.SEMANTIC_INDEX)

        candidates = self._compatible_sources(criteria.source)
        notes: list[str] = []
        if criteria.source is not None and not candidates:
            notes.append(f"Источник «{criteria.source}» не зарегистрирован в source registry.")
        if criteria.snapshot_id is not None:
            notes.append(
                "Статусы Росфинмониторинга берутся из snapshot и сопоставлений; обновление "
                "источников статей их не меняет."
            )
        if semantic:
            notes.append(
                f"Семантический поиск отбирает до {self._candidate_pool_size} кандидатов; "
                "все остальные критерии и факты проверяются по PostgreSQL."
            )
        return ResearchPlan(
            retrieval_mode=(
                (
                    ResearchRetrievalMode.HYBRID_RERANKED
                    if self._rerank_semantic
                    else ResearchRetrievalMode.HYBRID
                )
                if semantic
                else ResearchRetrievalMode.STRUCTURED
            ),
            candidate_pool_size=self._candidate_pool_size if semantic else 0,
            steps=[_RETRIEVE_STEP, *_STEPS] if semantic else list(_STEPS),
            data_requirements=requirements,
            candidate_sources=candidates,
            notes=notes,
        )

    def route(self, plan: ResearchPlan, response: ResearchResponse) -> SourceRoutingDecision:
        if response.total_matched > 0:
            return SourceRoutingDecision(
                source_refresh_required=False, reason=SourceRoutingReason.DATABASE_SUFFICIENT
            )
        if response.request.criteria.person_id is not None:
            return SourceRoutingDecision(
                source_refresh_required=False, reason=SourceRoutingReason.REFRESH_CANNOT_HELP
            )
        # A refresh means discovering new articles, so the source must support it.
        refreshable = [source for source in plan.candidate_sources if source.supports_discovery]
        if not refreshable:
            return SourceRoutingDecision(
                source_refresh_required=False, reason=SourceRoutingReason.NO_COMPATIBLE_SOURCE
            )
        return SourceRoutingDecision(
            source_refresh_required=True,
            sources=[source.source_id for source in refreshable],
            reason=SourceRoutingReason.NO_MATCHES_IN_DATABASE,
        )

    def _compatible_sources(self, source_filter: str | None) -> list[SourceCapability]:
        if source_filter is None:
            return list(self._capabilities)
        return [
            capability
            for capability in self._capabilities
            if capability.source_name == source_filter
        ]

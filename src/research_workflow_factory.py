"""Composition root for the natural-language research workflow."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.orm import Session, sessionmaker

from candidate_query_service import CandidateQueryService
from research_planning.planner import ResearchPlanner
from research_repository import SqlAlchemyPersonResearchRepository
from research_service import ResearchService
from research_workflow.graph import ResearchGraph, build_research_graph
from research_workflow.intake import LlmResearchRequestParser
from rosfinmonitoring_snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup
from semantic_retrieval.factory import SemanticRetrievalConfig, create_semantic_components
from semantic_retrieval.models import RetrievalBackend
from source_registry import SOURCES
from together_llm_client import TogetherConfig, TogetherStructuredLlmClient


def create_research_graph(
    session_factory: sessionmaker[Session],
    env: Mapping[str, str] | None = None,
) -> ResearchGraph:
    """Wire Together AI intake, ResearchService and PostgreSQL lookups.

    Raises `LlmConfigurationError` when Together AI is not configured.
    Semantic retrieval is wired only when QDRANT_URL is set; no model is loaded
    until the first semantic request.
    """
    llm_client = TogetherStructuredLlmClient(TogetherConfig.from_env(env))
    # Semantic retrieval is optional: without QDRANT_URL, structured requests
    # work and semantic ones fail with semantic_retrieval_not_configured.
    semantic = SemanticRetrievalConfig.from_env(env)
    components = (
        create_semantic_components(session_factory, semantic, env)
        if semantic.qdrant_url is not None
        else None
    )
    return build_research_graph(
        request_parser=LlmResearchRequestParser(llm_client),
        research_service=ResearchService(
            repository=SqlAlchemyPersonResearchRepository(session_factory),
            candidate_query=CandidateQueryService(session_factory),
        ),
        snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory),
        planner=ResearchPlanner(
            SOURCES,
            rerank_semantic=semantic.rerank,
            candidate_pool_size=semantic.candidate_pool_size,
        ),
        candidate_retriever=(
            components.retriever(
                RetrievalBackend.HYBRID_RERANKED if semantic.rerank else RetrievalBackend.HYBRID
            )
            if components is not None
            else None
        ),
        relevance_policy=components.relevance_policy() if components is not None else None,
    )

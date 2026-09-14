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
from source_registry import SOURCES
from together_llm_client import TogetherConfig, TogetherStructuredLlmClient


def create_research_graph(
    session_factory: sessionmaker[Session],
    env: Mapping[str, str] | None = None,
) -> ResearchGraph:
    """Wire Together AI intake, ResearchService and PostgreSQL lookups.

    Raises `LlmConfigurationError` when Together AI is not configured.
    """
    llm_client = TogetherStructuredLlmClient(TogetherConfig.from_env(env))
    return build_research_graph(
        request_parser=LlmResearchRequestParser(llm_client),
        research_service=ResearchService(
            repository=SqlAlchemyPersonResearchRepository(session_factory),
            candidate_query=CandidateQueryService(session_factory),
        ),
        snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory),
        planner=ResearchPlanner(SOURCES),
    )

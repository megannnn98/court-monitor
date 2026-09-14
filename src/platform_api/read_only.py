"""Read-only platform operations over the existing application services.

Every operation delegates: research to `ResearchService` / the deterministic
research workflow, monitoring to the monitoring repository and finding
service. Nothing here writes, ingests, merges or reviews.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from candidates.service import CandidateQueryService
from monitoring.findings import MonitoringFindingService
from monitoring.models import MonitoringFindingView, MonitoringStatusView
from monitoring.repository import SqlAlchemyMonitoringRepository
from research.models import PersonResearchResult, ResearchRequest, ResearchResponse
from research.planning.planner import ResearchPlanner
from research.repository import SqlAlchemyPersonResearchRepository
from research.service import (
    ResearchCandidatesRequiredError,
    ResearchService,
    ResearchSnapshotNotFoundError,
)
from research.workflow.graph import build_research_graph, run_research_query
from research.workflow.intake import PreparedRequestParser
from research.workflow.models import ResearchQueryResult
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup
from sources.source_registry import SOURCES

# Bounded responses for tool callers (the HTTP API allows up to 1000).
MAX_PLATFORM_RESULTS = 100
MAX_PLATFORM_FINDINGS = 100


class PlatformError(ValueError):
    """A caller error with a stable code (never a stack trace)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PersonDetails(BaseModel):
    found: bool
    person: PersonResearchResult | None = None


class FindingsPage(BaseModel):
    findings: list[MonitoringFindingView] = Field(default_factory=list)
    limit: int
    offset: int


def _validated_request(payload: dict[str, Any]) -> ResearchRequest:
    try:
        request = ResearchRequest.model_validate(payload)
    except ValidationError as exc:
        raise PlatformError(
            "invalid_research_request", f"invalid ResearchRequest: {exc.error_count()} error(s)"
        ) from exc
    if request.limit > MAX_PLATFORM_RESULTS:
        raise PlatformError(
            "limit_too_large", f"limit must be at most {MAX_PLATFORM_RESULTS} for this interface"
        )
    if request.criteria.semantic_query is not None:
        raise PlatformError(
            "semantic_query_not_supported",
            "semantic_query needs semantic retrieval, which this read-only interface does not run",
        )
    return request


class ReadOnlyPlatform:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._research = ResearchService(
            repository=SqlAlchemyPersonResearchRepository(session_factory),
            candidate_query=CandidateQueryService(session_factory),
        )
        self._monitoring = SqlAlchemyMonitoringRepository(session_factory)
        self._findings = MonitoringFindingService(session_factory)

    def research_people(self, request: dict[str, Any]) -> ResearchResponse:
        """Structured, deterministic research (`ResearchService.execute`)."""
        validated = _validated_request(request)
        try:
            return self._research.execute(validated)
        except ResearchSnapshotNotFoundError as exc:
            raise PlatformError("snapshot_not_found", str(exc)) from exc
        except ResearchCandidatesRequiredError as exc:  # guarded above; kept for safety
            raise PlatformError("semantic_query_not_supported", str(exc)) from exc

    def get_person(self, person_id: int, snapshot_id: int | None = None) -> PersonDetails:
        if person_id < 1:
            raise PlatformError("invalid_person_id", "person_id must be positive")
        criteria: dict[str, Any] = {"person_id": person_id}
        if snapshot_id is not None:
            criteria["snapshot_id"] = snapshot_id
        response = self.research_people({"object_type": "person", "criteria": criteria, "limit": 1})
        return PersonDetails(
            found=bool(response.results),
            person=response.results[0] if response.results else None,
        )

    def get_research_report(self, request: dict[str, Any]) -> ResearchQueryResult:
        """The deterministic research workflow from a structured request: snapshot
        defaulting, research, review policy, evidence-backed report. No LLM."""
        # Not validated as a ResearchRequest here: the workflow first resolves the
        # snapshot (rosfinmonitoring_status without snapshot_id → latest import)
        # and then validates, returning a failed/clarification result if invalid.
        criteria = request.get("criteria") or {}
        limit = request.get("limit", 20)
        if not isinstance(criteria, dict) or not isinstance(limit, int):
            raise PlatformError("invalid_research_request", "criteria must be an object")
        if limit > MAX_PLATFORM_RESULTS:
            raise PlatformError(
                "limit_too_large",
                f"limit must be at most {MAX_PLATFORM_RESULTS} for this interface",
            )
        if criteria.get("semantic_query") is not None:
            raise PlatformError(
                "semantic_query_not_supported",
                "semantic_query needs semantic retrieval, which this read-only interface does not run",
            )
        graph = build_research_graph(
            request_parser=PreparedRequestParser(request),
            research_service=self._research,
            snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(self._session_factory),
            planner=ResearchPlanner(SOURCES),
        )
        return run_research_query(graph, "structured request via platform interface")

    def list_monitoring_findings(
        self, *, active_only: bool = True, limit: int = 50, offset: int = 0
    ) -> FindingsPage:
        if not 1 <= limit <= MAX_PLATFORM_FINDINGS or offset < 0:
            raise PlatformError(
                "invalid_page", f"limit must be 1..{MAX_PLATFORM_FINDINGS}, offset >= 0"
            )
        return FindingsPage(
            findings=self._findings.list_findings(
                active_only=active_only, limit=limit, offset=offset
            ),
            limit=limit,
            offset=offset,
        )

    def get_monitoring_status(self) -> MonitoringStatusView:
        return self._monitoring.status()

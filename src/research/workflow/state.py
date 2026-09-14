"""Typed LangGraph state for the research workflow."""

from __future__ import annotations

from typing import Any, TypedDict

from research.models import ResearchRequest, ResearchResponse
from research.planning.models import ResearchPlan
from research.reports.evaluation import ResearchResultEvaluation
from research.reports.models import ResearchReport
from research.workflow.models import (
    ResearchIntake,
    ResearchQueryResult,
    UnsupportedCriterion,
    WorkflowError,
)
from semantic_retrieval.models import RetrievalResult
from semantic_retrieval.relevance import SemanticRetrievalDecision


class ResearchGraphState(TypedDict, total=False):
    """All workflow data lives here, not in any LLM conversation history.

    Nodes return partial updates; list fields are replaced as a whole.
    """

    raw_query: str
    intake: ResearchIntake
    # Structured candidate from intake, after deterministic snapshot
    # resolution; not yet validated.
    request_payload: dict[str, Any] | None
    structured_request: ResearchRequest
    research_plan: ResearchPlan
    # Retrieved candidates (ranking) and the relevance decision (acceptance).
    retrieval: RetrievalResult
    semantic_decision: SemanticRetrievalDecision
    research_response: ResearchResponse
    evaluation: ResearchResultEvaluation
    report: ResearchReport
    unsupported_criteria: list[UnsupportedCriterion]
    warnings: list[str]
    errors: list[WorkflowError]
    clarification_required: bool
    clarification_question: str | None
    review_required: bool
    final_result: ResearchQueryResult

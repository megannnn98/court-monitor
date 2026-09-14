"""Typed LangGraph state for the research workflow."""

from __future__ import annotations

from typing import Any, TypedDict

from research_models import ResearchRequest, ResearchResponse
from research_planning.models import ResearchPlan
from research_reports.evaluation import ResearchResultEvaluation
from research_reports.models import ResearchReport
from research_workflow.models import (
    ResearchIntake,
    ResearchQueryResult,
    UnsupportedCriterion,
    WorkflowError,
)


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

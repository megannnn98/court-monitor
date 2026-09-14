"""Deterministic assembly of the workflow result. No LLM involved.

Results from ResearchService are passed through unchanged, so statuses,
evidence, sources and review flags cannot be reworded or lost.
"""

from __future__ import annotations

from research_workflow.models import (
    ResearchQueryResult,
    UnsupportedCriterion,
    WorkflowStatus,
)
from research_workflow.state import ResearchGraphState


def completed_result(state: ResearchGraphState) -> ResearchQueryResult:
    response = state["research_response"]
    return ResearchQueryResult(
        status=WorkflowStatus.COMPLETED,
        query=state["raw_query"],
        request=state["structured_request"],
        results=response.results,
        total_matched=response.total_matched,
        warnings=state.get("warnings", []),
        plan=state.get("research_plan"),
        report=state.get("report"),
        retrieval=state.get("retrieval"),
    )


def clarification_result(state: ResearchGraphState) -> ResearchQueryResult:
    return ResearchQueryResult(
        status=WorkflowStatus.CLARIFICATION_REQUIRED,
        query=state["raw_query"],
        request=state.get("structured_request"),
        unsupported_criteria=state.get("unsupported_criteria", []),
        warnings=state.get("warnings", []),
        clarification_question=state.get("clarification_question"),
    )


def failed_result(state: ResearchGraphState) -> ResearchQueryResult:
    errors = state.get("errors", [])
    return ResearchQueryResult(
        status=WorkflowStatus.FAILED,
        query=state["raw_query"],
        warnings=state.get("warnings", []),
        error=errors[0] if errors else None,
    )


def unsupported_criteria_question(criteria: list[UnsupportedCriterion]) -> str:
    listed = "; ".join(f"{item.criterion} («{item.value}»)" for item in criteria)
    return (
        f"Запрос содержит критерии, которые система пока не умеет проверять: {listed}. "
        "Поиск не выполнялся, чтобы не выдать более широкий список за ответ на исходный "
        "запрос. Уберите эти условия или переформулируйте запрос."
    )

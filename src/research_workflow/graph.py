"""LangGraph orchestration: natural language -> ResearchRequest -> ResearchService.

The graph orchestrates; it never queries the database, re-derives statuses
or asks an LLM to phrase facts. ResearchService decides the result.
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Literal, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from research_models import ResearchRequest, ResearchResponse
from research_service import ResearchSnapshotNotFoundError
from research_workflow.assembly import (
    clarification_result,
    completed_result,
    failed_result,
    unsupported_criteria_question,
)
from research_workflow.intake import ResearchRequestParser
from research_workflow.llm import (
    LlmAuthenticationError,
    LlmConfigurationError,
    LlmError,
    LlmInvalidResponseError,
    LlmRateLimitError,
    LlmRequestRejectedError,
    LlmTimeoutError,
    LlmUnavailableError,
)
from research_workflow.models import (
    ResearchQueryResult,
    RosfinmonitoringSnapshotSummary,
    WorkflowError,
    WorkflowErrorCode,
)
from research_workflow.state import ResearchGraphState

logger = logging.getLogger("research_workflow")

type ResearchGraph = CompiledStateGraph[
    ResearchGraphState, None, ResearchGraphState, ResearchGraphState
]

_LLM_ERROR_CODES: list[tuple[type[LlmError], WorkflowErrorCode]] = [
    (LlmConfigurationError, WorkflowErrorCode.LLM_NOT_CONFIGURED),
    (LlmTimeoutError, WorkflowErrorCode.LLM_TIMEOUT),
    (LlmUnavailableError, WorkflowErrorCode.LLM_UNAVAILABLE),
    (LlmAuthenticationError, WorkflowErrorCode.LLM_AUTHENTICATION_FAILED),
    (LlmRateLimitError, WorkflowErrorCode.LLM_RATE_LIMITED),
    (LlmRequestRejectedError, WorkflowErrorCode.LLM_REQUEST_REJECTED),
    (LlmInvalidResponseError, WorkflowErrorCode.LLM_INVALID_OUTPUT),
]


class ResearchExecutor(Protocol):
    """The deterministic ResearchService from the research domain."""

    def execute(self, request: ResearchRequest) -> ResearchResponse: ...


class RosfinmonitoringSnapshotLookup(Protocol):
    def latest_imported_snapshot(self) -> RosfinmonitoringSnapshotSummary | None:
        """Most recent snapshot that has imported entries, or None."""
        ...


def _llm_error_code(error: LlmError) -> WorkflowErrorCode:
    for error_type, code in _LLM_ERROR_CODES:
        if isinstance(error, error_type):
            return code
    return WorkflowErrorCode.LLM_UNAVAILABLE


def _number_in_text(number: object, text: str) -> bool:
    return re.search(rf"(?<!\d){re.escape(str(number))}(?!\d)", text) is not None


def build_research_graph(
    *,
    request_parser: ResearchRequestParser,
    research_service: ResearchExecutor,
    snapshot_lookup: RosfinmonitoringSnapshotLookup,
) -> ResearchGraph:
    def request_intake(state: ResearchGraphState) -> ResearchGraphState:
        try:
            intake = request_parser.parse(state["raw_query"])
        except LlmError as exc:
            code = _llm_error_code(exc)
            logger.warning("request_intake_failed code=%s error=%s", code.value, type(exc).__name__)
            return {"errors": [WorkflowError(code=code, message=str(exc))]}

        logger.info(
            "request_parsed has_request=%s unsupported_criteria=%d clarification=%s",
            intake.request is not None,
            len(intake.unsupported_criteria),
            intake.clarification_question is not None,
        )
        return {
            "intake": intake,
            "request_payload": intake.request,
            "unsupported_criteria": intake.unsupported_criteria,
        }

    def route_after_intake(
        state: ResearchGraphState,
    ) -> Literal["failed", "clarification", "resolve_snapshot"]:
        if state.get("errors"):
            return "failed"
        intake = state["intake"]
        if (
            intake.unsupported_criteria
            or intake.clarification_question is not None
            or intake.request is None
        ):
            return "clarification"
        return "resolve_snapshot"

    def resolve_snapshot(state: ResearchGraphState) -> ResearchGraphState:
        """Deterministic snapshot defaulting; the LLM never picks a snapshot."""
        payload = copy.deepcopy(state.get("request_payload")) or {}
        criteria = payload.get("criteria")
        if not isinstance(criteria, dict):
            return {}

        warnings = list(state.get("warnings", []))
        snapshot_id = criteria.get("snapshot_id")
        if snapshot_id is not None and not _number_in_text(snapshot_id, state["raw_query"]):
            warnings.append(
                f"Snapshot #{snapshot_id} не упоминается в запросе пользователя; "
                "значение от LLM проигнорировано."
            )
            criteria.pop("snapshot_id")
            snapshot_id = None

        if criteria.get("rosfinmonitoring_status") is not None and snapshot_id is None:
            snapshot = snapshot_lookup.latest_imported_snapshot()
            if snapshot is None:
                return {
                    "warnings": warnings,
                    "errors": [
                        WorkflowError(
                            code=WorkflowErrorCode.NO_ROSFINMONITORING_SNAPSHOT,
                            message=(
                                "Нет ни одного импортированного snapshot Росфинмониторинга; "
                                "проверить статус в перечне невозможно."
                            ),
                        )
                    ],
                }
            criteria["snapshot_id"] = snapshot.snapshot_id
            warnings.append(
                "Snapshot не указан пользователем; использован последний доступный "
                f"snapshot #{snapshot.snapshot_id} от {snapshot.snapshot_date.date().isoformat()}."
            )
            if snapshot.match_count == 0:
                warnings.append(
                    f"Для snapshot #{snapshot.snapshot_id} сопоставление с Росфинмониторингом "
                    "ещё не запускалось: статусы будут no_match_record."
                )
            logger.info("snapshot_resolved snapshot_id=%d", snapshot.snapshot_id)

        return {"request_payload": payload, "warnings": warnings}

    def route_after_snapshot(state: ResearchGraphState) -> Literal["failed", "validate_request"]:
        return "failed" if state.get("errors") else "validate_request"

    def validate_request(state: ResearchGraphState) -> ResearchGraphState:
        try:
            request = ResearchRequest.model_validate(state.get("request_payload"))
        except ValidationError as exc:
            errors = exc.errors()
            # Our own domain rules (e.g. date_from > date_to) raise value
            # errors: the user can fix those. Anything else (unknown field,
            # wrong enum or type) means the LLM broke the schema.
            if all(error["type"] == "value_error" for error in errors):
                messages = "; ".join(str(error["msg"]) for error in errors)
                logger.info("request_validation status=invalid_domain errors=%d", len(errors))
                return {
                    "clarification_question": (
                        f"Запрос нельзя выполнить: {messages}. Уточните критерии поиска."
                    )
                }
            logger.warning("request_validation status=invalid_schema errors=%d", len(errors))
            return {
                "errors": [
                    WorkflowError(
                        code=WorkflowErrorCode.LLM_INVALID_OUTPUT,
                        message=(
                            "LLM вернул запрос, не соответствующий ResearchRequest: "
                            f"{len(errors)} ошибок валидации."
                        ),
                    )
                ]
            }
        logger.info("request_validation status=valid")
        return {"structured_request": request}

    def route_after_validation(
        state: ResearchGraphState,
    ) -> Literal["failed", "clarification", "research"]:
        if state.get("errors"):
            return "failed"
        if state.get("clarification_question") is not None:
            return "clarification"
        return "research"

    def research(state: ResearchGraphState) -> ResearchGraphState:
        try:
            response = research_service.execute(state["structured_request"])
        except ResearchSnapshotNotFoundError as exc:
            return {
                "clarification_question": (
                    f"Snapshot #{exc.snapshot_id} не найден. Укажите существующий snapshot "
                    "или не указывайте его — тогда будет использован последний."
                )
            }
        logger.info(
            "research_executed result_count=%d total_matched=%d",
            len(response.results),
            response.total_matched,
        )
        return {
            "research_response": response,
            "review_required": any(result.review_required for result in response.results),
        }

    def route_after_research(state: ResearchGraphState) -> Literal["clarification", "assemble"]:
        return "clarification" if state.get("clarification_question") is not None else "assemble"

    def clarification(state: ResearchGraphState) -> ResearchGraphState:
        questions: list[str] = []
        unsupported = state.get("unsupported_criteria", [])
        if unsupported:
            questions.append(unsupported_criteria_question(unsupported))
        intake = state.get("intake")
        if intake is not None and intake.clarification_question is not None:
            questions.append(intake.clarification_question)
        if state.get("clarification_question") is not None and not questions:
            questions.append(str(state["clarification_question"]))
        if not questions:
            questions.append("Не удалось понять запрос. Уточните, кого или что нужно найти.")

        question = " ".join(questions)
        logger.info("clarification_required unsupported_criteria=%d", len(unsupported))
        updated: ResearchGraphState = {
            **state,
            "clarification_required": True,
            "clarification_question": question,
        }
        return {
            "clarification_required": True,
            "clarification_question": question,
            "final_result": clarification_result(updated),
        }

    def workflow_failed(state: ResearchGraphState) -> ResearchGraphState:
        errors = state.get("errors", [])
        logger.error("workflow_failed code=%s", errors[0].code.value if errors else "unknown")
        return {"final_result": failed_result(state)}

    def assemble_response(state: ResearchGraphState) -> ResearchGraphState:
        return {"final_result": completed_result(state)}

    builder = StateGraph(ResearchGraphState)
    builder.add_node("request_intake", request_intake)
    builder.add_node("resolve_snapshot", resolve_snapshot)
    builder.add_node("validate_request", validate_request)
    builder.add_node("research", research)
    builder.add_node("clarification", clarification)
    builder.add_node("workflow_failed", workflow_failed)
    builder.add_node("assemble_response", assemble_response)

    builder.add_edge(START, "request_intake")
    builder.add_conditional_edges(
        "request_intake",
        route_after_intake,
        {
            "failed": "workflow_failed",
            "clarification": "clarification",
            "resolve_snapshot": "resolve_snapshot",
        },
    )
    builder.add_conditional_edges(
        "resolve_snapshot",
        route_after_snapshot,
        {"failed": "workflow_failed", "validate_request": "validate_request"},
    )
    builder.add_conditional_edges(
        "validate_request",
        route_after_validation,
        {"failed": "workflow_failed", "clarification": "clarification", "research": "research"},
    )
    builder.add_conditional_edges(
        "research",
        route_after_research,
        {"clarification": "clarification", "assemble": "assemble_response"},
    )
    builder.add_edge("clarification", END)
    builder.add_edge("workflow_failed", END)
    builder.add_edge("assemble_response", END)
    return builder.compile()


def run_research_query(graph: ResearchGraph, query: str) -> ResearchQueryResult:
    logger.info("workflow_started query_chars=%d", len(query))
    try:
        state = graph.invoke({"raw_query": query})
    except Exception as exc:
        logger.exception("workflow_failed code=unexpected error=%s", type(exc).__name__)
        raise
    result: ResearchQueryResult = state["final_result"]
    logger.info(
        "workflow_finished status=%s result_count=%d review_required=%s",
        result.status.value,
        len(result.results),
        result.review_required,
    )
    return result

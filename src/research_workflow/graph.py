"""LangGraph orchestration: natural language -> ResearchRequest -> ResearchService -> report.

The graph orchestrates; it never queries the database, re-derives statuses
or asks an LLM to phrase facts. ResearchService decides the result; the
planner, review policy and report builder (all deterministic) present it.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from research_models import ResearchRequest, ResearchResponse
from research_planning.models import ResearchRetrievalMode
from research_planning.planner import ResearchPlanner
from research_reports.builder import ResearchReportBuilder
from research_reports.evaluation import ResearchResultEvaluator
from research_reports.review_policy import ResearchReviewPolicy
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
    UnsupportedCriterion,
    WorkflowError,
    WorkflowErrorCode,
    WorkflowStatus,
)
from research_workflow.snapshot_references import extract_explicit_snapshot_ids
from research_workflow.state import ResearchGraphState
from semantic_retrieval.models import (
    RetrievalEntityType,
    RetrievalError,
    RetrievalNotConfiguredError,
    RetrievalQuery,
)
from semantic_retrieval.retrievers import EntityRetriever

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

    def execute(
        self,
        request: ResearchRequest,
        *,
        candidate_person_ids: Sequence[int] | None = None,
    ) -> ResearchResponse: ...


class RosfinmonitoringSnapshotLookup(Protocol):
    def latest_imported_snapshot(self) -> RosfinmonitoringSnapshotSummary | None:
        """Most recent snapshot that has imported entries, or None."""
        ...


def _llm_error_code(error: LlmError) -> WorkflowErrorCode:
    for error_type, code in _LLM_ERROR_CODES:
        if isinstance(error, error_type):
            return code
    return WorkflowErrorCode.LLM_UNAVAILABLE


# Pydantic error types caused by values the user asked for (fixable by the
# user). Everything else means the LLM broke the ResearchRequest contract.
_USER_FIXABLE_ERROR_TYPES = frozenset(
    {
        "value_error",
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
        "too_short",
        "too_long",
    }
)


@dataclass(frozen=True)
class ValidationIssues:
    unsupported_criteria: list[UnsupportedCriterion]
    user_messages: list[str]
    contract_errors: int


def classify_validation_errors(error: ValidationError) -> ValidationIssues:
    unsupported: list[UnsupportedCriterion] = []
    messages: list[str] = []
    contract_errors = 0
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"])
        if item["type"] == "extra_forbidden":
            value = item.get("input")
            unsupported.append(
                UnsupportedCriterion(
                    criterion=str(item["loc"][-1]),
                    value=value
                    if isinstance(value, str)
                    else json.dumps(value, ensure_ascii=False),
                )
            )
        elif item["type"] in _USER_FIXABLE_ERROR_TYPES:
            messages.append(f"{location}: {item['msg']}" if location else str(item["msg"]))
        else:
            contract_errors += 1
    return ValidationIssues(unsupported, messages, contract_errors)


def build_research_graph(
    *,
    request_parser: ResearchRequestParser,
    research_service: ResearchExecutor,
    snapshot_lookup: RosfinmonitoringSnapshotLookup,
    planner: ResearchPlanner,
    review_policy: ResearchReviewPolicy | None = None,
    report_builder: ResearchReportBuilder | None = None,
    candidate_retriever: EntityRetriever | None = None,
) -> ResearchGraph:
    """`candidate_retriever` is needed only for plans with semantic retrieval;
    structured requests never touch it (or Qdrant)."""
    evaluator = ResearchResultEvaluator(
        planner=planner, review_policy=review_policy or ResearchReviewPolicy()
    )
    report_builder = report_builder or ResearchReportBuilder()

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
        # Only snapshot references the user wrote explicitly count; the LLM's
        # snapshot_id is never trusted on its own.
        explicit_ids = extract_explicit_snapshot_ids(state["raw_query"])
        llm_snapshot_id = criteria.pop("snapshot_id", None)
        snapshot_id: int | None = None
        if len(explicit_ids) > 1:
            listed = ", ".join(f"#{item}" for item in explicit_ids)
            return {
                "warnings": warnings,
                "clarification_question": (
                    f"В запросе указано несколько snapshot: {listed}. Укажите один snapshot."
                ),
            }
        if explicit_ids:
            snapshot_id = explicit_ids[0]
            if llm_snapshot_id is not None and str(llm_snapshot_id) != str(snapshot_id):
                warnings.append(
                    f"LLM указал snapshot #{llm_snapshot_id}, но в запросе явно указан "
                    f"snapshot #{snapshot_id}; использован #{snapshot_id}."
                )
            criteria["snapshot_id"] = snapshot_id
        elif llm_snapshot_id is not None:
            warnings.append(
                f"Snapshot #{llm_snapshot_id} не упоминается в запросе пользователя; "
                "значение от LLM проигнорировано."
            )

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

    def route_after_snapshot(
        state: ResearchGraphState,
    ) -> Literal["failed", "clarification", "validate_request"]:
        if state.get("errors"):
            return "failed"
        if state.get("clarification_question") is not None:
            return "clarification"
        return "validate_request"

    def validate_request(state: ResearchGraphState) -> ResearchGraphState:
        try:
            request = ResearchRequest.model_validate(state.get("request_payload"))
        except ValidationError as exc:
            issues = classify_validation_errors(exc)
            if issues.contract_errors:
                logger.warning(
                    "request_validation status=invalid_schema errors=%d", issues.contract_errors
                )
                return {
                    "errors": [
                        WorkflowError(
                            code=WorkflowErrorCode.LLM_INVALID_OUTPUT,
                            message=(
                                "LLM вернул запрос, не соответствующий ResearchRequest: "
                                f"{issues.contract_errors} ошибок валидации."
                            ),
                        )
                    ]
                }
            logger.info(
                "request_validation status=needs_clarification unsupported=%d invalid_values=%d",
                len(issues.unsupported_criteria),
                len(issues.user_messages),
            )
            update: ResearchGraphState = {}
            if issues.unsupported_criteria:
                update["unsupported_criteria"] = [
                    *state.get("unsupported_criteria", []),
                    *issues.unsupported_criteria,
                ]
            if issues.user_messages:
                update["clarification_question"] = (
                    f"Запрос нельзя выполнить: {'; '.join(issues.user_messages)}. "
                    "Уточните критерии поиска."
                )
            return update
        logger.info("request_validation status=valid")
        return {"structured_request": request}

    def route_after_validation(
        state: ResearchGraphState,
    ) -> Literal["failed", "clarification", "build_research_plan"]:
        if state.get("errors"):
            return "failed"
        if state.get("clarification_question") is not None or (
            "structured_request" not in state and state.get("unsupported_criteria")
        ):
            return "clarification"
        return "build_research_plan"

    def build_research_plan(state: ResearchGraphState) -> ResearchGraphState:
        plan = planner.plan(state["structured_request"])
        logger.info(
            "research_plan_built requirements=%s candidate_sources=%d",
            ",".join(requirement.value for requirement in plan.data_requirements),
            len(plan.candidate_sources),
        )
        return {"research_plan": plan}

    def route_after_plan(state: ResearchGraphState) -> Literal["retrieve_candidates", "research"]:
        if state["research_plan"].retrieval_mode is ResearchRetrievalMode.STRUCTURED:
            return "research"
        return "retrieve_candidates"

    def retrieve_candidates(state: ResearchGraphState) -> ResearchGraphState:
        """Candidate ids only; facts are loaded by ResearchService afterwards."""
        request = state["structured_request"]
        plan = state["research_plan"]
        semantic_query = request.criteria.semantic_query
        assert semantic_query is not None  # guaranteed by the planner's routing
        try:
            if candidate_retriever is None:
                raise RetrievalNotConfiguredError(
                    "Semantic retrieval is not configured (set QDRANT_URL and build the index)"
                )
            retrieval = candidate_retriever.retrieve(
                RetrievalQuery(
                    text=semantic_query,
                    entity_type=RetrievalEntityType.PERSON,
                    limit=plan.candidate_pool_size,
                )
            )
        except RetrievalError as exc:
            code = (
                WorkflowErrorCode.SEMANTIC_RETRIEVAL_NOT_CONFIGURED
                if isinstance(exc, RetrievalNotConfiguredError)
                else WorkflowErrorCode.SEMANTIC_RETRIEVAL_UNAVAILABLE
            )
            logger.warning(
                "candidate_retrieval_failed code=%s error=%s", code.value, type(exc).__name__
            )
            return {
                "errors": [
                    WorkflowError(
                        code=code,
                        message=(
                            f"Семантический поиск недоступен ({exc}); это ошибка, "
                            "а не пустой результат."
                        ),
                    )
                ]
            }
        logger.info(
            "candidates_retrieved backend=%s count=%d pool_size=%d",
            retrieval.backend.value,
            len(retrieval.hits),
            plan.candidate_pool_size,
        )
        return {"retrieval": retrieval}

    def route_after_retrieval(state: ResearchGraphState) -> Literal["failed", "research"]:
        return "failed" if state.get("errors") else "research"

    def research(state: ResearchGraphState) -> ResearchGraphState:
        try:
            retrieval = state.get("retrieval")
            response = research_service.execute(
                state["structured_request"],
                candidate_person_ids=None if retrieval is None else retrieval.entity_ids,
            )
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
        return {"research_response": response}

    def route_after_research(
        state: ResearchGraphState,
    ) -> Literal["clarification", "evaluate_result"]:
        if state.get("clarification_question") is not None:
            return "clarification"
        return "evaluate_result"

    def evaluate_result(state: ResearchGraphState) -> ResearchGraphState:
        evaluation = evaluator.evaluate(
            request=state["structured_request"],
            plan=state["research_plan"],
            response=state["research_response"],
        )
        logger.info(
            "result_evaluated review_required_count=%d source_refresh_required=%s",
            sum(review.decision.required for review in evaluation.reviews),
            evaluation.routing.source_refresh_required,
        )
        return {"evaluation": evaluation, "review_required": evaluation.review_required}

    def build_report(state: ResearchGraphState) -> ResearchGraphState:
        report = report_builder.build(
            request=state["structured_request"],
            response=state["research_response"],
            evaluation=state["evaluation"],
            plan=state["research_plan"],
            retrieval=state.get("retrieval"),
        )
        logger.info("report_built status=%s items=%d", report.status.value, len(report.items))
        return {"report": report}

    def human_review_gate(state: ResearchGraphState) -> ResearchGraphState:
        """Marks review-required results in the final result.

        Read-only: no review record is created here; that is an explicit
        action (POST /research/reviews).
        """
        report = state["report"]
        logger.info(
            "human_review_gate review_required=%s review_required_count=%d",
            report.review_required,
            report.summary.review_required_count,
        )
        return {"review_required": report.review_required, "final_result": completed_result(state)}

    def clarification(state: ResearchGraphState) -> ResearchGraphState:
        questions: list[str] = []
        unsupported = state.get("unsupported_criteria", [])
        if unsupported:
            questions.append(unsupported_criteria_question(unsupported))
        intake = state.get("intake")
        if intake is not None and intake.clarification_question is not None:
            questions.append(intake.clarification_question)
        if state.get("clarification_question") is not None:
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

    builder = StateGraph(ResearchGraphState)
    builder.add_node("request_intake", request_intake)
    builder.add_node("resolve_snapshot", resolve_snapshot)
    builder.add_node("validate_request", validate_request)
    builder.add_node("build_research_plan", build_research_plan)
    builder.add_node("retrieve_candidates", retrieve_candidates)
    builder.add_node("research", research)
    builder.add_node("evaluate_result", evaluate_result)
    builder.add_node("build_report", build_report)
    builder.add_node("human_review_gate", human_review_gate)
    builder.add_node("clarification", clarification)
    builder.add_node("workflow_failed", workflow_failed)

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
        {
            "failed": "workflow_failed",
            "clarification": "clarification",
            "validate_request": "validate_request",
        },
    )
    builder.add_conditional_edges(
        "validate_request",
        route_after_validation,
        {
            "failed": "workflow_failed",
            "clarification": "clarification",
            "build_research_plan": "build_research_plan",
        },
    )
    builder.add_conditional_edges(
        "build_research_plan",
        route_after_plan,
        {"retrieve_candidates": "retrieve_candidates", "research": "research"},
    )
    builder.add_conditional_edges(
        "retrieve_candidates",
        route_after_retrieval,
        {"failed": "workflow_failed", "research": "research"},
    )
    builder.add_conditional_edges(
        "research",
        route_after_research,
        {"clarification": "clarification", "evaluate_result": "evaluate_result"},
    )
    builder.add_edge("evaluate_result", "build_report")
    builder.add_edge("build_report", "human_review_gate")
    builder.add_edge("clarification", END)
    builder.add_edge("workflow_failed", END)
    builder.add_edge("human_review_gate", END)
    return builder.compile()


def run_research_query(graph: ResearchGraph, query: str) -> ResearchQueryResult:
    logger.info("workflow_started query_chars=%d", len(query))
    try:
        state = graph.invoke({"raw_query": query})
    except Exception as exc:
        # Exception text (e.g. SQLAlchemy "[parameters: ...]") can carry user
        # criteria; above DEBUG only the exception type is logged.
        logger.error(
            "workflow_failed code=%s error=%s",
            WorkflowErrorCode.WORKFLOW_UNEXPECTED_ERROR.value,
            type(exc).__name__,
        )
        logger.debug("workflow_unexpected_error_traceback", exc_info=True)
        return ResearchQueryResult(
            status=WorkflowStatus.FAILED,
            query=query,
            error=WorkflowError(
                code=WorkflowErrorCode.WORKFLOW_UNEXPECTED_ERROR,
                message=f"Внутренняя ошибка workflow ({type(exc).__name__}); это не пустой результат.",
            ),
        )
    result: ResearchQueryResult = state["final_result"]
    logger.info(
        "workflow_finished status=%s result_count=%d review_required=%s",
        result.status.value,
        len(result.results),
        result.review_required,
    )
    return result

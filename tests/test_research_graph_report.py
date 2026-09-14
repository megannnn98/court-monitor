"""Compiled graph: planning, evaluation, report and human review gate."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from research_report_fixtures import SNAPSHOT_ID, person_result, request, response, rosfin
from research_workflow_fakes import FakeRequestParser, FakeResearchService, FakeSnapshotLookup

from candidate_query_models import RosfinmonitoringStatus
from research_planning.models import SourceRoutingReason
from research_planning.planner import ResearchPlanner
from research_reports.models import ResearchReportStatus, ResearchReviewReason
from research_service import ResearchSnapshotNotFoundError
from research_workflow.graph import ResearchGraph, build_research_graph, run_research_query
from research_workflow.models import (
    ResearchIntake,
    RosfinmonitoringSnapshotSummary,
    UnsupportedCriterion,
    WorkflowStatus,
)
from source_registry import SOURCES

LATEST = RosfinmonitoringSnapshotSummary(
    snapshot_id=SNAPSHOT_ID,
    snapshot_date=datetime(2026, 9, 1, tzinfo=UTC),
    entry_count=10,
    match_count=5,
)


def _graph(parser: FakeRequestParser, service: FakeResearchService) -> ResearchGraph:
    return build_research_graph(
        request_parser=parser,
        research_service=service,
        snapshot_lookup=FakeSnapshotLookup(latest=LATEST),
        planner=ResearchPlanner(SOURCES),
    )


def _parser(criteria: dict[str, Any] | None = None) -> FakeRequestParser:
    return FakeRequestParser(
        intake=ResearchIntake(request={"object_type": "person", "criteria": criteria or {}})
    )


def _visited(graph: ResearchGraph, query: str) -> list[str]:
    nodes: list[str] = []
    for update in graph.stream({"raw_query": query}, stream_mode="updates"):
        nodes.extend(update)
    return nodes


def test_valid_request_goes_plan_research_evaluate_report_review_gate() -> None:
    graph = _graph(_parser({"persecution_status": "political"}), FakeResearchService())

    assert _visited(graph, "Политически преследуемые") == [
        "request_intake",
        "resolve_snapshot",
        "validate_request",
        "build_research_plan",
        "research",
        "evaluate_result",
        "build_report",
        "human_review_gate",
    ]


def test_clarification_never_reaches_planning_or_research() -> None:
    parser = FakeRequestParser(
        intake=ResearchIntake(
            request={"object_type": "person", "criteria": {"persecution_status": "political"}},
            unsupported_criteria=[UnsupportedCriterion(criterion="region", value="из Казани")],
        )
    )
    service = FakeResearchService()
    graph = _graph(parser, service)

    visited = _visited(graph, "Политические из Казани")

    assert "build_research_plan" not in visited
    assert "research" not in visited
    assert service.requests == []
    result = run_research_query(graph, "Политические из Казани")
    assert result.status is WorkflowStatus.CLARIFICATION_REQUIRED
    assert (result.plan, result.report) == (None, None)


def test_review_condition_yields_report_with_review_not_failure() -> None:
    research_request = request(snapshot_id=SNAPSHOT_ID)
    ambiguous = person_result(rf=rosfin(RosfinmonitoringStatus.AMBIGUOUS))
    service = FakeResearchService(response=response(research_request, [ambiguous]))

    result = run_research_query(_graph(_parser(), service), "Все")

    assert result.status is WorkflowStatus.COMPLETED
    assert result.error is None
    assert result.report is not None
    assert result.report.status is ResearchReportStatus.REVIEW_REQUIRED
    assert result.review_required is True
    (item,) = result.report.items
    assert [reason.code for reason in item.review.reasons] == [
        ResearchReviewReason.ROSFIN_AMBIGUOUS
    ]


def test_raw_results_are_kept_next_to_the_report() -> None:
    research_request = request()
    result_person = person_result()
    service = FakeResearchService(response=response(research_request, [result_person]))

    result = run_research_query(_graph(_parser(), service), "Все")

    assert result.results == [result_person]
    assert result.total_matched == 1
    assert result.report is not None
    assert result.report.items[0].person_id == result_person.person.id


def test_empty_database_result_recommends_refresh_after_searching_first() -> None:
    service = FakeResearchService()

    result = run_research_query(_graph(_parser({"persecution_status": "political"}), service), "x")

    # Database first: the search ran once before any recommendation.
    assert len(service.requests) == 1
    assert result.status is WorkflowStatus.COMPLETED
    assert result.report is not None
    assert result.report.status is ResearchReportStatus.INSUFFICIENT_DATA
    assert result.report.source_refresh_recommended is True
    assert set(result.report.recommended_sources) == set(SOURCES)
    assert result.report.source_routing.reason is SourceRoutingReason.NO_MATCHES_IN_DATABASE


def test_plan_is_part_of_the_result_and_respects_source_filter() -> None:
    result = run_research_query(
        _graph(_parser({"source": "SOTA"}), FakeResearchService()), "Люди из SOTA"
    )

    assert result.plan is not None
    assert [source.source_id for source in result.plan.candidate_sources] == ["sota-vision"]
    assert result.report is not None
    assert result.report.recommended_sources == ["sota-vision"]


def test_unknown_snapshot_is_still_clarification_without_report() -> None:
    service = FakeResearchService(error=ResearchSnapshotNotFoundError(99))

    result = run_research_query(
        _graph(_parser({"rosfinmonitoring_status": "not_matched", "snapshot_id": 99}), service),
        "snapshot 99",
    )

    assert result.status is WorkflowStatus.CLARIFICATION_REQUIRED
    assert result.report is None


def test_gate_logs_report_status_and_review(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="research_workflow")
    research_request = request(snapshot_id=SNAPSHOT_ID)
    service = FakeResearchService(
        response=response(
            research_request, [person_result(rf=rosfin(RosfinmonitoringStatus.INSUFFICIENT_DATA))]
        )
    )

    run_research_query(_graph(_parser(), service), "Все")

    messages = [record.getMessage() for record in caplog.records]
    assert any("research_plan_built" in message for message in messages)
    assert any(
        "result_evaluated review_required_count=1 source_refresh_required=False" in message
        for message in messages
    )
    assert any("report_built status=review_required" in message for message in messages)
    assert any("human_review_gate review_required=True" in message for message in messages)

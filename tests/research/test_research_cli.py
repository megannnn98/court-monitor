"""CLI adapter: argv -> ResearchRequest, ResearchResponse -> text."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from support.research_report_fixtures import ARTICLE_URL, person_result, rosfin
from support.research_report_fixtures import request as report_request
from support.research_report_fixtures import response as report_response

from candidates.models import RosfinmonitoringStatus
from extraction.models import EventEntityRole, EventType
from persecution.models import PersecutionClassification, PersecutionClassificationStatus
from persons.models import Person
from research.cli import (
    ResearchCliError,
    add_ask_arguments,
    add_research_arguments,
    ask_exit_code,
    build_research_request,
    format_query_result,
    format_research_plan,
    format_research_response,
    format_structured_request,
)
from research.mapping import build_warnings
from research.models import (
    PersonResearchCriteria,
    PersonResearchResult,
    ResearchEvent,
    ResearchObjectType,
    ResearchRequest,
    ResearchResponse,
    ResearchRosfinmonitoring,
    ResearchSource,
)
from research.planning.models import ResearchRetrievalMode
from research.planning.planner import ResearchPlanner
from research.reports.builder import ResearchReportBuilder
from research.reports.evaluation import ResearchResultEvaluator
from research.reports.review_policy import ResearchReviewPolicy
from research.workflow.models import (
    ResearchQueryResult,
    UnsupportedCriterion,
    WorkflowError,
    WorkflowErrorCode,
    WorkflowStatus,
)
from semantic_retrieval.models import RetrievalBackend
from sources.source_registry import SOURCES


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_research_arguments(subparsers)
    return parser.parse_args(["research", *argv])


def test_arguments_map_to_research_request() -> None:
    args = _parse(
        [
            "--object",
            "person",
            "--persecution-status",
            "political",
            "--rosfin-status",
            "not_matched",
            "--snapshot-id",
            "3",
            "--event-type",
            "arrest",
            "--event-type",
            "sentence",
            "--date-from",
            "2024-01-01",
            "--source",
            "ovd-info",
            "--name",
            "Иванов",
            "--limit",
            "5",
        ]
    )

    request = build_research_request(args)

    assert request.object_type is ResearchObjectType.PERSON
    assert request.limit == 5
    criteria = request.criteria
    assert criteria.persecution_status is PersecutionClassificationStatus.POLITICAL
    assert criteria.rosfinmonitoring_status is RosfinmonitoringStatus.NOT_MATCHED
    assert criteria.snapshot_id == 3
    assert criteria.event_types == [EventType.ARREST, EventType.SENTENCE]
    assert criteria.date_from == date(2024, 1, 1)
    # Registry key is translated to the stored source name.
    assert criteria.source == "ОВД-Инфо"
    assert criteria.name == "Иванов"


def test_invalid_combination_is_reported_as_cli_error() -> None:
    args = _parse(["--rosfin-status", "not_matched"])

    with pytest.raises(ResearchCliError, match="rosfinmonitoring_status requires snapshot_id"):
        build_research_request(args)


def test_text_output_shows_person_statuses_events_sources_and_review() -> None:
    result = PersonResearchResult(
        person=Person(
            id=7,
            canonical_name="Иван Иванов",
            normalized_name="иван иванов",
            matching_key="иван|иванов",
        ),
        persecution=PersecutionClassification(
            person_id=7,
            status=PersecutionClassificationStatus.POLITICAL,
            confidence=0.85,
            classifier_name="rule-based",
            classifier_version="1.0.0",
        ),
        rosfinmonitoring=ResearchRosfinmonitoring(
            snapshot_id=3, status=RosfinmonitoringStatus.AMBIGUOUS, confidence=0.6
        ),
        events=[
            ResearchEvent(
                event_id=11,
                event_type=EventType.ARREST,
                event_date=datetime(2024, 3, 5, tzinfo=UTC),
                roles=[EventEntityRole.SUBJECT],
                confidence=0.8,
                article_id=4,
            )
        ],
        sources=[
            ResearchSource(
                article_id=4,
                article_title="Хроника",
                source_name="ОВД-Инфо",
                url="https://ovd.info/news/1",
            )
        ],
    )
    result.warnings = build_warnings(result.persecution, result.rosfinmonitoring)
    response = ResearchResponse(
        object_type=ResearchObjectType.PERSON,
        request=ResearchRequest(object_type=ResearchObjectType.PERSON),
        results=[result],
        total_matched=1,
    )

    text = format_research_response(response)

    assert "Matched 1 person(s), showing 1" in text
    assert "#7 Иван Иванов" in text
    assert "persecution: political (0.85)" in text
    assert "rosfinmonitoring[snapshot 3]: ambiguous (0.60)" in text
    assert "review_required: yes" in text
    assert "rosfin_ambiguous" in text
    assert "2024-03-05 arrest [subject] article 4" in text
    assert "[4] ОВД-Инфо: Хроника https://ovd.info/news/1" in text


def _ask_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_ask_arguments(subparsers)
    return parser.parse_args(["ask", *argv])


def test_ask_arguments() -> None:
    args = _ask_args(["Найди Иванова", "--show-request", "--json"])

    assert (args.query, args.show_request, args.json, args.verbose) == (
        "Найди Иванова",
        True,
        True,
        False,
    )


def _completed(results: list[PersonResearchResult]) -> ResearchQueryResult:
    return ResearchQueryResult(
        status=WorkflowStatus.COMPLETED,
        query="q",
        request=ResearchRequest(
            object_type=ResearchObjectType.PERSON,
            criteria=PersonResearchCriteria(
                persecution_status=PersecutionClassificationStatus.POLITICAL,
                rosfinmonitoring_status=RosfinmonitoringStatus.NOT_MATCHED,
                snapshot_id=7,
            ),
        ),
        results=results,
        total_matched=len(results),
        warnings=["Snapshot не указан пользователем; использован последний доступный snapshot #7"],
    )


def test_show_request_prints_only_structured_request() -> None:
    text = format_structured_request(_completed([]))

    assert json.loads(text) == {
        "request": {
            "object_type": "person",
            "criteria": {
                "persecution_status": "political",
                "rosfinmonitoring_status": "not_matched",
                "snapshot_id": 7,
            },
            "limit": 20,
        },
        "unsupported_criteria": [],
    }


def test_completed_output_shows_warnings_results_and_exit_zero() -> None:
    result = _completed([])

    text = format_query_result(result)

    assert "! Snapshot не указан пользователем" in text
    assert "Matched 0 person(s), showing 0" in text
    assert ask_exit_code(result) == 0


def test_failure_and_clarification_have_distinct_output_and_exit_codes() -> None:
    failed = ResearchQueryResult(
        status=WorkflowStatus.FAILED,
        query="q",
        error=WorkflowError(code=WorkflowErrorCode.LLM_TIMEOUT, message="timed out"),
    )
    clarification = ResearchQueryResult(
        status=WorkflowStatus.CLARIFICATION_REQUIRED,
        query="q",
        unsupported_criteria=[UnsupportedCriterion(criterion="region", value="из Казани")],
        clarification_question="Уберите регион.",
    )

    assert "Workflow failed [llm_timeout]: timed out" in format_query_result(failed)
    assert "not an empty result" in format_query_result(failed)
    assert "Clarification required: Уберите регион." in format_query_result(clarification)
    assert "unsupported: region = из Казани" in format_query_result(clarification)
    assert (ask_exit_code(failed), ask_exit_code(clarification)) == (2, 3)


def test_ask_without_together_config_exits_with_configuration_error() -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("TOGETHER_") and key not in {"DATABASE_URL", "TEST_DATABASE_URL"}
    }
    # The engine is created lazily; nothing connects to this URL.
    env["DATABASE_URL"] = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"

    completed = subprocess.run(
        [sys.executable, "src/main.py", "ask", "Найди политически преследуемых людей"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 1
    assert completed.stderr.strip().endswith(
        "Together AI is not configured: set TOGETHER_API_KEY, TOGETHER_MODEL"
    )
    assert completed.stdout == ""


# --- ask: report output ------------------------------------------------------------


def _report_result(
    results: list[PersonResearchResult], research_request: ResearchRequest | None = None
) -> ResearchQueryResult:
    research_request = research_request or report_request(
        persecution_status=PersecutionClassificationStatus.POLITICAL,
        rosfinmonitoring_status=RosfinmonitoringStatus.NOT_MATCHED,
        snapshot_id=7,
    )
    research_response = report_response(research_request, results)
    planner = ResearchPlanner(SOURCES)
    plan = planner.plan(research_request)
    evaluation = ResearchResultEvaluator(
        planner=planner, review_policy=ResearchReviewPolicy()
    ).evaluate(request=research_request, plan=plan, response=research_response)
    return ResearchQueryResult(
        status=WorkflowStatus.COMPLETED,
        query="q",
        request=research_request,
        results=results,
        total_matched=research_response.total_matched,
        plan=plan,
        report=ResearchReportBuilder().build(
            request=research_request, response=research_response, evaluation=evaluation
        ),
    )


def test_ask_report_arguments() -> None:
    args = _ask_args(["Найди", "--raw", "--show-plan"])

    assert (args.raw, args.show_plan) == (True, True)


def test_ask_prints_report_with_facts_reasons_sources_and_review() -> None:
    text = format_query_result(_report_result([person_result(rf=rosfin())]))

    assert "Report: complete" in text
    assert "#1 Иван Иванов" in text
    assert "Persecution: political (0.85)" in text
    assert "Rosfinmonitoring: not_matched (0.80)" in text
    assert "- Антивоенная деятельность" in text
    assert (
        "- persecution_status: requested political (confidence ≥ 0.70); actual political (0.85)"
        in text
    )
    assert f"ОВД-Инфо — Арест за пикет — {ARTICLE_URL}" in text
    assert "Review: not required" in text
    assert "Source refresh: not recommended (database_sufficient)" in text


def test_ask_report_shows_review_reasons() -> None:
    research_request = ResearchRequest(
        object_type=ResearchObjectType.PERSON, criteria=PersonResearchCriteria(snapshot_id=7)
    )
    text = format_query_result(
        _report_result(
            [person_result(rf=rosfin(RosfinmonitoringStatus.AMBIGUOUS))], research_request
        )
    )

    assert "Report: review_required" in text
    assert "Review: required (blocking)" in text
    assert "- rosfin_ambiguous:" in text


def test_ask_report_shows_refresh_recommendation_for_empty_result() -> None:
    research_request = ResearchRequest(
        object_type=ResearchObjectType.PERSON,
        criteria=PersonResearchCriteria(
            persecution_status=PersecutionClassificationStatus.POLITICAL
        ),
    )

    text = format_query_result(_report_result([], research_request))

    assert "Report: insufficient_data" in text
    assert "Source refresh: recommended (ovd-info, sota-vision, sudrf-2zovs, tg-" in text
    assert "— not executed" in text


def test_raw_prints_the_research_response_format() -> None:
    text = format_query_result(_report_result([person_result(rf=rosfin())]), raw=True)

    assert "Matched 1 person(s), showing 1" in text
    assert "Report:" not in text


def test_show_plan_prints_plan_json_only() -> None:
    payload = json.loads(format_research_plan(_report_result([])))

    assert payload["database_search"] is True
    assert {source["source_id"] for source in payload["candidate_sources"]} == set(SOURCES)


def test_ask_report_shows_structured_retrieval_mode() -> None:
    text = format_query_result(_report_result([person_result(rf=rosfin())]))

    assert "Retrieval: structured" in text
    assert "Retrieval rank" not in text


def test_ask_report_shows_semantic_candidates_without_scores() -> None:
    semantic_request = report_request(semantic_query="антивоенные публикации")
    result = _report_result([person_result()], semantic_request)
    assert result.report is not None
    report = result.report.model_copy(
        update={
            "retrieval": result.report.retrieval.model_copy(
                update={
                    "mode": ResearchRetrievalMode.HYBRID,
                    "backend": RetrievalBackend.HYBRID,
                    "candidate_pool_size": 100,
                    "candidates_returned": 7,
                    "candidates_accepted": 3,
                    "min_similarity": 0.8,
                }
            ),
            "items": [result.report.items[0].model_copy(update={"retrieval_rank": 2})],
        }
    )

    text = format_query_result(result.model_copy(update={"report": report}))

    assert (
        "Retrieval: hybrid (retrieved 7, accepted 3, pool 100, min similarity 0.80) "
        "— candidates, not facts"
    ) in text
    assert "Retrieval rank: 2 (similarity, not a fact)" in text
    assert "score" not in text

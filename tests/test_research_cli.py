"""CLI adapter: argv -> ResearchRequest, ResearchResponse -> text."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime

import pytest

from candidate_query_models import RosfinmonitoringStatus
from extraction_models import EventEntityRole, EventType
from persecution_models import PersecutionClassification, PersecutionClassificationStatus
from person_models import Person
from research_cli import (
    ResearchCliError,
    add_ask_arguments,
    add_research_arguments,
    ask_exit_code,
    build_research_request,
    format_query_result,
    format_research_response,
    format_structured_request,
)
from research_mapping import build_warnings
from research_models import (
    PersonResearchCriteria,
    PersonResearchResult,
    ResearchEvent,
    ResearchObjectType,
    ResearchRequest,
    ResearchResponse,
    ResearchRosfinmonitoring,
    ResearchSource,
)
from research_workflow.models import (
    ResearchQueryResult,
    UnsupportedCriterion,
    WorkflowError,
    WorkflowErrorCode,
    WorkflowStatus,
)


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

"""Compiled research graph end-to-end with fake dependencies."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from research_workflow_fakes import (
    FakeRequestParser,
    FakeResearchService,
    FakeSnapshotLookup,
    FakeStructuredLlmClient,
)

from candidate_query_models import RosfinmonitoringStatus
from persecution_models import PersecutionClassification, PersecutionClassificationStatus
from person_models import Person
from research_mapping import build_warnings
from research_models import (
    PersonResearchResult,
    ResearchEvidence,
    ResearchEvidenceType,
    ResearchObjectType,
    ResearchRequest,
    ResearchResponse,
    ResearchRosfinmonitoring,
    ResearchSource,
)
from research_planning.planner import ResearchPlanner
from research_service import ResearchSnapshotNotFoundError
from research_workflow.graph import build_research_graph, run_research_query
from research_workflow.intake import LlmResearchRequestParser
from research_workflow.llm import (
    LlmAuthenticationError,
    LlmError,
    LlmInvalidResponseError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmUnavailableError,
)
from research_workflow.models import (
    ResearchIntake,
    ResearchQueryResult,
    RosfinmonitoringSnapshotSummary,
    UnsupportedCriterion,
    WorkflowErrorCode,
    WorkflowStatus,
)
from source_registry import SOURCES

LATEST_SNAPSHOT = RosfinmonitoringSnapshotSummary(
    snapshot_id=7,
    snapshot_date=datetime(2026, 9, 1, tzinfo=UTC),
    entry_count=100,
    match_count=40,
)


def _intake(criteria: dict[str, Any] | None = None, **extra: Any) -> ResearchIntake:
    return ResearchIntake(request={"object_type": "person", "criteria": criteria or {}}, **extra)


def _run(
    query: str,
    *,
    parser: FakeRequestParser | LlmResearchRequestParser,
    service: FakeResearchService | None = None,
    lookup: FakeSnapshotLookup | None = None,
) -> ResearchQueryResult:
    graph = build_research_graph(
        request_parser=parser,
        research_service=service or FakeResearchService(),
        snapshot_lookup=lookup or FakeSnapshotLookup(latest=LATEST_SNAPSHOT),
        planner=ResearchPlanner(SOURCES),
    )
    return run_research_query(graph, query)


def _result(
    *,
    persecution: PersecutionClassificationStatus,
    rf: RosfinmonitoringStatus,
) -> PersonResearchResult:
    classification = PersecutionClassification(
        person_id=1,
        status=persecution,
        confidence=0.6,
        classifier_name="rule-based",
        classifier_version="1.0.0",
    )
    rosfin = ResearchRosfinmonitoring(snapshot_id=7, status=rf, confidence=0.5)
    return PersonResearchResult(
        person=Person(
            id=1, canonical_name="Иван Иванов", normalized_name="иван иванов", matching_key="k"
        ),
        persecution=classification,
        rosfinmonitoring=rosfin,
        evidence=[
            ResearchEvidence(
                evidence_type=ResearchEvidenceType.PERSON_MENTION,
                article_id=4,
                extraction_run_id=2,
                start_offset=10,
                end_offset=23,
                text="Ивана Иванова",
                mention_id=11,
            )
        ],
        sources=[
            ResearchSource(
                article_id=4,
                article_title="Хроника",
                source_name="ОВД-Инфо",
                url="https://ovd.info/1",
            )
        ],
        warnings=build_warnings(classification, rosfin),
    )


def _response_with(result: PersonResearchResult) -> ResearchResponse:
    request = ResearchRequest(object_type=ResearchObjectType.PERSON)
    return ResearchResponse(
        object_type=ResearchObjectType.PERSON, request=request, results=[result], total_matched=1
    )


# --- routing: valid -> research -> assemble ---------------------------------------


def test_valid_request_runs_research_and_assembles_result() -> None:
    parser = FakeRequestParser(intake=_intake({"persecution_status": "political"}))
    service = FakeResearchService()

    result = _run("Найди политически преследуемых людей.", parser=parser, service=service)

    assert result.status is WorkflowStatus.COMPLETED
    assert [r.criteria.persecution_status for r in service.requests] == [
        PersecutionClassificationStatus.POLITICAL
    ]
    # Scenario A: nothing beyond what the user asked for.
    assert service.requests[0].criteria.rosfinmonitoring_status is None
    assert result.request == service.requests[0]
    assert result.error is None
    assert result.clarification_required is False


def test_zero_matches_is_a_completed_result_not_a_failure() -> None:
    result = _run("Найди Иванова.", parser=FakeRequestParser(intake=_intake({"name": "Иванов"})))

    # Scenario C: an incomplete name is searched, not sent back for clarification.
    assert result.status is WorkflowStatus.COMPLETED
    assert result.request is not None and result.request.criteria.name == "Иванов"
    assert (result.results, result.total_matched, result.error) == ([], 0, None)


# --- snapshot resolution ---------------------------------------------------------


def test_rf_status_without_snapshot_uses_latest_imported_snapshot() -> None:
    parser = FakeRequestParser(
        intake=_intake(
            {"persecution_status": "political", "rosfinmonitoring_status": "not_matched"}
        )
    )
    service = FakeResearchService()

    result = _run(
        "Найди политически преследуемых людей, которых нет в Росфинмониторинге.",
        parser=parser,
        service=service,
    )

    assert result.status is WorkflowStatus.COMPLETED
    (executed,) = service.requests
    # Scenario B: POLITICAL + NOT_MATCHED against the resolved snapshot.
    assert executed.criteria.persecution_status is PersecutionClassificationStatus.POLITICAL
    assert executed.criteria.rosfinmonitoring_status is RosfinmonitoringStatus.NOT_MATCHED
    assert executed.criteria.snapshot_id == 7
    assert result.request is not None and result.request.criteria.snapshot_id == 7
    assert result.warnings == [
        "Snapshot не указан пользователем; использован последний доступный snapshot #7 от 2026-09-01."
    ]


def test_explicit_snapshot_from_user_is_kept_without_lookup() -> None:
    lookup = FakeSnapshotLookup(latest=LATEST_SNAPSHOT)
    service = FakeResearchService()

    result = _run(
        "Кого нет в перечне по snapshot 3?",
        parser=FakeRequestParser(
            intake=_intake({"rosfinmonitoring_status": "not_matched", "snapshot_id": 3})
        ),
        service=service,
        lookup=lookup,
    )

    assert service.requests[0].criteria.snapshot_id == 3
    assert lookup.calls == 0
    assert result.warnings == []


def test_snapshot_id_invented_by_llm_is_ignored_and_resolved_deterministically() -> None:
    service = FakeResearchService()

    result = _run(
        "Кого нет в перечне Росфинмониторинга?",
        parser=FakeRequestParser(
            intake=_intake({"rosfinmonitoring_status": "not_matched", "snapshot_id": 42})
        ),
        service=service,
    )

    assert service.requests[0].criteria.snapshot_id == 7
    assert result.warnings[0] == (
        "Snapshot #42 не упоминается в запросе пользователя; значение от LLM проигнорировано."
    )


def test_latest_snapshot_without_matching_run_is_flagged() -> None:
    unmatched = LATEST_SNAPSHOT.model_copy(update={"match_count": 0})

    result = _run(
        "Кого нет в перечне?",
        parser=FakeRequestParser(intake=_intake({"rosfinmonitoring_status": "not_matched"})),
        lookup=FakeSnapshotLookup(latest=unmatched),
    )

    assert result.warnings[-1] == (
        "Для snapshot #7 сопоставление с Росфинмониторингом ещё не запускалось: "
        "статусы будут no_match_record."
    )


def test_rf_status_without_any_imported_snapshot_fails_without_research() -> None:
    service = FakeResearchService()

    result = _run(
        "Кого нет в перечне?",
        parser=FakeRequestParser(intake=_intake({"rosfinmonitoring_status": "not_matched"})),
        service=service,
        lookup=FakeSnapshotLookup(latest=None),
    )

    assert result.status is WorkflowStatus.FAILED
    assert result.error is not None
    assert result.error.code is WorkflowErrorCode.NO_ROSFINMONITORING_SNAPSHOT
    assert service.requests == []


def test_no_rf_criterion_does_not_touch_snapshots() -> None:
    lookup = FakeSnapshotLookup(latest=LATEST_SNAPSHOT)

    _run(
        "Политические",
        parser=FakeRequestParser(intake=_intake({"persecution_status": "political"})),
        lookup=lookup,
    )

    assert lookup.calls == 0


# --- routing: ambiguous / unsupported -> clarification -> END --------------------


def test_unsupported_criteria_require_clarification_and_skip_research() -> None:
    unsupported = [
        UnsupportedCriterion(criterion="occupation", value="программисты"),
        UnsupportedCriterion(criterion="age", value="30–35 лет"),
        UnsupportedCriterion(criterion="region", value="из Казани"),
    ]
    service = FakeResearchService()
    lookup = FakeSnapshotLookup(latest=LATEST_SNAPSHOT)

    result = _run(
        "Найди политически преследуемых программистов 30–35 лет из Казани.",
        parser=FakeRequestParser(
            intake=_intake({"persecution_status": "political"}, unsupported_criteria=unsupported)
        ),
        service=service,
        lookup=lookup,
    )

    # Scenario D: preserved and reported, never silently dropped or partially run.
    assert result.status is WorkflowStatus.CLARIFICATION_REQUIRED
    assert result.clarification_required is True
    assert result.unsupported_criteria == unsupported
    assert result.clarification_question is not None
    for fragment in ["occupation («программисты»)", "age («30–35 лет»)", "region («из Казани»)"]:
        assert fragment in result.clarification_question
    assert "Поиск не выполнялся" in result.clarification_question
    assert (service.requests, lookup.calls, result.results) == ([], 0, [])


def test_ambiguous_query_returns_llm_clarification_question() -> None:
    service = FakeResearchService()

    result = _run(
        "Найди его.",
        parser=FakeRequestParser(
            intake=ResearchIntake(clarification_question="Кого именно нужно найти?")
        ),
        service=service,
    )

    assert result.status is WorkflowStatus.CLARIFICATION_REQUIRED
    assert result.clarification_question == "Кого именно нужно найти?"
    assert result.request is None
    assert service.requests == []


def test_domain_rule_violation_asks_for_clarification() -> None:
    service = FakeResearchService()

    result = _run(
        "С марта по январь",
        parser=FakeRequestParser(
            intake=_intake({"date_from": "2024-03-01", "date_to": "2024-01-01"})
        ),
        service=service,
    )

    assert result.status is WorkflowStatus.CLARIFICATION_REQUIRED
    assert result.clarification_question is not None
    assert "date_from must not be after date_to" in result.clarification_question
    assert service.requests == []


def test_unknown_user_snapshot_asks_for_clarification() -> None:
    result = _run(
        "Кого нет в перечне snapshot 99?",
        parser=FakeRequestParser(
            intake=_intake({"rosfinmonitoring_status": "not_matched", "snapshot_id": 99})
        ),
        service=FakeResearchService(error=ResearchSnapshotNotFoundError(99)),
    )

    assert result.status is WorkflowStatus.CLARIFICATION_REQUIRED
    assert result.clarification_question is not None
    assert "Snapshot #99 не найден" in result.clarification_question


# --- routing: invalid -> controlled error ----------------------------------------


@pytest.mark.parametrize(
    "llm_output",
    [
        # Envelope broken: not an intake object at all.
        {"answer": "Иванов не в перечне Росфинмониторинга"},
        # Envelope fine, request breaks the ResearchRequest schema.
        # (An unknown criteria field is a clarification, see the unsupported test.)
        {"request": {"object_type": "article"}},
        {
            "request": {
                "object_type": "person",
                "criteria": {"persecution_status": "very_political"},
            }
        },
    ],
)
def test_invalid_llm_output_is_controlled_failure(llm_output: dict[str, Any]) -> None:
    # Scenario E, through the real LLM parser with a fake provider.
    service = FakeResearchService()
    parser = LlmResearchRequestParser(FakeStructuredLlmClient(data=llm_output))

    result = _run("Найди кого-нибудь", parser=parser, service=service)

    assert result.status is WorkflowStatus.FAILED
    assert result.error is not None
    assert result.error.code is WorkflowErrorCode.LLM_INVALID_OUTPUT
    assert (result.results, result.total_matched, service.requests) == ([], None, [])


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (LlmTimeoutError("timed out"), WorkflowErrorCode.LLM_TIMEOUT),
        (LlmUnavailableError("503"), WorkflowErrorCode.LLM_UNAVAILABLE),
        (LlmAuthenticationError("401"), WorkflowErrorCode.LLM_AUTHENTICATION_FAILED),
        (LlmRateLimitError("429"), WorkflowErrorCode.LLM_RATE_LIMITED),
        (LlmInvalidResponseError("not json"), WorkflowErrorCode.LLM_INVALID_OUTPUT),
    ],
)
def test_provider_errors_are_distinguishable_from_empty_results(
    error: LlmError, code: WorkflowErrorCode
) -> None:
    service = FakeResearchService()

    result = _run(
        "Найди политически преследуемых", parser=FakeRequestParser(error=error), service=service
    )

    assert result.status is WorkflowStatus.FAILED
    assert result.error is not None and result.error.code is code
    assert result.total_matched is None
    assert service.requests == []


# --- facts are passed through unchanged -----------------------------------------


def test_ambiguous_rf_status_is_not_turned_into_not_matched() -> None:
    # Scenario F.
    person = _result(
        persecution=PersecutionClassificationStatus.POLITICAL, rf=RosfinmonitoringStatus.AMBIGUOUS
    )

    result = _run(
        "Политические",
        parser=FakeRequestParser(intake=_intake({"persecution_status": "political"})),
        service=FakeResearchService(response=_response_with(person)),
    )

    (returned,) = result.results
    assert returned.rosfinmonitoring is not None
    assert returned.rosfinmonitoring.status is RosfinmonitoringStatus.AMBIGUOUS


def test_uncertain_persecution_is_preserved() -> None:
    # Scenario G.
    person = _result(
        persecution=PersecutionClassificationStatus.UNCERTAIN, rf=RosfinmonitoringStatus.NOT_MATCHED
    )

    result = _run(
        "Все",
        parser=FakeRequestParser(intake=_intake()),
        service=FakeResearchService(response=_response_with(person)),
    )

    (returned,) = result.results
    assert returned.persecution is not None
    assert returned.persecution.status is PersecutionClassificationStatus.UNCERTAIN


def test_review_requirement_and_provenance_survive_the_graph() -> None:
    # Scenario H.
    person = _result(
        persecution=PersecutionClassificationStatus.UNCERTAIN, rf=RosfinmonitoringStatus.AMBIGUOUS
    )

    result = _run(
        "Все",
        parser=FakeRequestParser(intake=_intake()),
        service=FakeResearchService(response=_response_with(person)),
    )

    assert result.review_required is True
    (returned,) = result.results
    assert returned == person
    assert returned.review_required is True
    assert result.model_dump(mode="json")["results"][0] == person.model_dump(mode="json")


# --- observability ----------------------------------------------------------------


def test_workflow_logs_lifecycle_without_query_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="research_workflow")
    secret_query = "Найди Секретного Человека"

    _run(secret_query, parser=FakeRequestParser(intake=_intake({"name": "Секретный"})))

    messages = [record.getMessage() for record in caplog.records]
    for event in [
        "workflow_started",
        "request_parsed",
        "request_validation status=valid",
        "research_executed result_count=0",
        "workflow_finished status=completed",
    ]:
        assert any(event in message for message in messages), event
    assert not any(secret_query in message for message in messages)


def test_failures_and_clarifications_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="research_workflow")

    _run("x", parser=FakeRequestParser(error=LlmTimeoutError("t")))
    _run("y", parser=FakeRequestParser(intake=ResearchIntake(clarification_question="?")))

    messages = [record.getMessage() for record in caplog.records]
    assert any("workflow_failed code=llm_timeout" in message for message in messages)
    assert any("clarification_required" in message for message in messages)


# --- explicit snapshot references decide, not the LLM -----------------------------


def test_unrelated_number_does_not_authorize_llm_snapshot_id() -> None:
    service = FakeResearchService()

    result = _run(
        "Найди 3 человека, которых нет в перечне Росфинмониторинга",
        parser=FakeRequestParser(
            intake=ResearchIntake(
                request={
                    "object_type": "person",
                    "criteria": {"rosfinmonitoring_status": "not_matched", "snapshot_id": 3},
                    "limit": 3,
                }
            )
        ),
        service=service,
    )

    (executed,) = service.requests
    assert (executed.criteria.snapshot_id, executed.limit) == (7, 3)
    assert result.warnings[0] == (
        "Snapshot #3 не упоминается в запросе пользователя; значение от LLM проигнорировано."
    )


def test_explicit_snapshot_in_query_is_used_even_if_llm_omitted_it() -> None:
    service = FakeResearchService()
    lookup = FakeSnapshotLookup(latest=LATEST_SNAPSHOT)

    result = _run(
        "Кого нет в перечне по снапшоту №4?",
        parser=FakeRequestParser(intake=_intake({"rosfinmonitoring_status": "not_matched"})),
        service=service,
        lookup=lookup,
    )

    assert service.requests[0].criteria.snapshot_id == 4
    assert lookup.calls == 0
    assert result.warnings == []


def test_explicit_snapshot_in_query_overrides_different_llm_value() -> None:
    service = FakeResearchService()

    result = _run(
        "Кого нет в перечне, snapshot 4",
        parser=FakeRequestParser(
            intake=_intake({"rosfinmonitoring_status": "not_matched", "snapshot_id": 44})
        ),
        service=service,
    )

    assert service.requests[0].criteria.snapshot_id == 4
    assert result.warnings == [
        "LLM указал snapshot #44, но в запросе явно указан snapshot #4; использован #4."
    ]


def test_several_explicit_snapshots_require_clarification() -> None:
    service = FakeResearchService()

    result = _run(
        "Сравни snapshot 3 и snapshot 5",
        parser=FakeRequestParser(
            intake=_intake({"rosfinmonitoring_status": "not_matched", "snapshot_id": 3})
        ),
        service=service,
    )

    assert result.status is WorkflowStatus.CLARIFICATION_REQUIRED
    assert result.clarification_question == (
        "В запросе указано несколько snapshot: #3, #5. Укажите один snapshot."
    )
    assert service.requests == []


# --- validation errors: user-fixable vs broken LLM contract -----------------------


def test_unknown_criteria_field_from_llm_becomes_unsupported_clarification() -> None:
    service = FakeResearchService()

    result = _run(
        "Найди политически преследуемых людей из Казани",
        parser=FakeRequestParser(
            intake=_intake({"persecution_status": "political", "region": "Казань"})
        ),
        service=service,
    )

    assert result.status is WorkflowStatus.CLARIFICATION_REQUIRED
    assert result.unsupported_criteria == [UnsupportedCriterion(criterion="region", value="Казань")]
    assert result.clarification_question is not None
    assert "region («Казань»)" in result.clarification_question
    assert result.error is None
    assert service.requests == []


@pytest.mark.parametrize(
    ("request_payload", "message_fragment"),
    [
        ({"object_type": "person", "limit": 5000}, "limit"),
        ({"object_type": "person", "criteria": {"event_types": []}}, "event_types"),
        ({"object_type": "person", "criteria": {"person_id": 0}}, "person_id"),
    ],
)
def test_out_of_bounds_values_ask_for_clarification_not_failure(
    request_payload: dict[str, Any], message_fragment: str
) -> None:
    service = FakeResearchService()

    result = _run(
        "Покажи 5000 человек",
        parser=FakeRequestParser(intake=ResearchIntake(request=request_payload)),
        service=service,
    )

    assert result.status is WorkflowStatus.CLARIFICATION_REQUIRED
    assert result.clarification_question is not None
    assert message_fragment in result.clarification_question
    assert result.error is None
    assert service.requests == []


def test_broken_contract_wins_over_user_fixable_errors() -> None:
    result = _run(
        "…",
        parser=FakeRequestParser(
            intake=_intake({"persecution_status": "very_political", "region": "Казань"})
        ),
    )

    assert result.status is WorkflowStatus.FAILED
    assert result.error is not None
    assert result.error.code is WorkflowErrorCode.LLM_INVALID_OUTPUT


def test_clarification_reports_both_unsupported_fields_and_invalid_values() -> None:
    result = _run(
        "Покажи 5000 человек из Казани",
        parser=FakeRequestParser(
            intake=ResearchIntake(
                request={"object_type": "person", "criteria": {"region": "Казань"}, "limit": 5000}
            )
        ),
    )

    assert result.status is WorkflowStatus.CLARIFICATION_REQUIRED
    assert result.clarification_question is not None
    assert "region («Казань»)" in result.clarification_question
    assert "limit" in result.clarification_question


# --- unexpected failures ------------------------------------------------------------


class _DatabaseError(Exception):
    pass


LEAKY_ERROR = _DatabaseError(
    "(psycopg.OperationalError) connection lost [SQL: SELECT persons.id WHERE name ILIKE %(p)s] "
    "[parameters: {'p': '%Иванов%'}]"
)


def test_unexpected_exception_is_structured_failure_not_crash() -> None:
    result = _run(
        "Найди Иванова",
        parser=FakeRequestParser(intake=_intake({"name": "Иванов"})),
        service=FakeResearchService(error=LEAKY_ERROR),
    )

    assert result.status is WorkflowStatus.FAILED
    assert result.error is not None
    assert result.error.code is WorkflowErrorCode.WORKFLOW_UNEXPECTED_ERROR
    assert "Иванов" not in result.error.message
    assert (result.results, result.total_matched) == ([], None)


def test_unexpected_exception_logs_type_only_above_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="research_workflow")

    _run(
        "Найди Иванова",
        parser=FakeRequestParser(intake=_intake({"name": "Иванов"})),
        service=FakeResearchService(error=LEAKY_ERROR),
    )

    assert any(
        "workflow_failed code=workflow_unexpected_error error=_DatabaseError" in r.getMessage()
        for r in caplog.records
    )
    for record in caplog.records:
        assert "Иванов" not in record.getMessage()
        assert record.exc_info is None
    assert caplog.text.count("Иванов") == 0


def test_unexpected_exception_traceback_only_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="research_workflow")

    _run(
        "Найди Иванова",
        parser=FakeRequestParser(intake=_intake({"name": "Иванов"})),
        service=FakeResearchService(error=LEAKY_ERROR),
    )

    debug_records = [r for r in caplog.records if r.exc_info is not None]
    assert [r.levelno for r in debug_records] == [logging.DEBUG]


# --- regression phrases from the stabilization checklist ---------------------------


def test_count_in_query_is_not_a_snapshot_id() -> None:
    service = FakeResearchService()

    result = _run(
        "Найди 3 политически преследуемых человека, которых нет в Росфинмониторинге",
        parser=FakeRequestParser(
            intake=ResearchIntake(
                request={
                    "object_type": "person",
                    "criteria": {
                        "persecution_status": "political",
                        "rosfinmonitoring_status": "not_matched",
                        "snapshot_id": 3,
                    },
                    "limit": 3,
                }
            )
        ),
        service=service,
    )

    (executed,) = service.requests
    assert (executed.criteria.snapshot_id, executed.limit) == (7, 3)
    assert result.status is WorkflowStatus.COMPLETED


def test_check_snapshot_hash_three_uses_snapshot_three() -> None:
    service = FakeResearchService()
    lookup = FakeSnapshotLookup(latest=LATEST_SNAPSHOT)

    _run(
        "проверь snapshot #3",
        parser=FakeRequestParser(intake=_intake({"rosfinmonitoring_status": "not_matched"})),
        service=service,
        lookup=lookup,
    )

    assert service.requests[0].criteria.snapshot_id == 3
    assert lookup.calls == 0


def test_two_hash_snapshots_require_clarification() -> None:
    service = FakeResearchService()

    result = _run(
        "snapshot #3 и snapshot #4",
        parser=FakeRequestParser(intake=_intake({"rosfinmonitoring_status": "not_matched"})),
        service=service,
    )

    assert result.status is WorkflowStatus.CLARIFICATION_REQUIRED
    assert result.clarification_question == (
        "В запросе указано несколько snapshot: #3, #4. Укажите один snapshot."
    )
    assert service.requests == []


@pytest.mark.parametrize(
    "request_payload",
    [
        # Required field missing.
        {"criteria": {"persecution_status": "political"}},
        # Wrong types.
        {"object_type": "person", "limit": "много"},
        {"object_type": "person", "criteria": {"event_types": "arrest"}},
        {"object_type": "person", "criteria": "political"},
    ],
)
def test_broken_llm_contract_fails_with_invalid_output(request_payload: dict[str, Any]) -> None:
    service = FakeResearchService()

    result = _run(
        "…",
        parser=FakeRequestParser(intake=ResearchIntake(request=request_payload)),
        service=service,
    )

    assert result.status is WorkflowStatus.FAILED
    assert result.error is not None
    assert result.error.code is WorkflowErrorCode.LLM_INVALID_OUTPUT
    assert service.requests == []

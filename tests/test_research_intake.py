"""Request intake: prompt, schema and LLM output handling (fake LLM)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from research_workflow_fakes import FakeStructuredLlmClient

from candidate_query_models import RosfinmonitoringStatus
from extraction_models import EventType
from persecution_models import PersecutionClassificationStatus
from research_workflow.intake import (
    INTAKE_SCHEMA_NAME,
    LlmResearchRequestParser,
    intake_json_schema,
    render_intake_system_prompt,
)
from research_workflow.llm import LlmInvalidResponseError
from research_workflow.models import ResearchIntake, UnsupportedCriterion


def _parser(data: dict[str, Any]) -> tuple[LlmResearchRequestParser, FakeStructuredLlmClient]:
    client = FakeStructuredLlmClient(data=data)
    return LlmResearchRequestParser(client, today=lambda: date(2026, 9, 14)), client


def test_prompt_states_the_intake_rules() -> None:
    prompt = render_intake_system_prompt()

    for rule in [
        "translate the user's research",
        "Do NOT answer the research question",
        "Do NOT invent facts or criteria",
        "It does NOT imply any Rosfinmonitoring status",
        "Never choose a snapshot yourself",
        "Use only the fields and values of the schema",
        "Report unsupported criteria explicitly",
        "Never\n   drop such a constraint silently",
        "Report ambiguity explicitly",
        "is\n   NOT ambiguous",
        "No prose, no\nexplanations, no reasoning.",
        "Set `semantic_query` only for a free-text description",
        "`semantic_query` is NOT a place for such constraints",
    ]:
        assert rule in prompt


def test_prompt_lists_every_domain_enum_value_and_embeds_schema() -> None:
    prompt = render_intake_system_prompt()

    for value in [
        *(s.value for s in PersecutionClassificationStatus),
        *(s.value for s in RosfinmonitoringStatus),
        *(t.value for t in EventType),
        "ОВД-Инфо",
        "SOTA",
    ]:
        assert f'"{value}"' in prompt
    assert "$" not in prompt.replace("$defs", "").replace("$ref", "")
    assert json.dumps(intake_json_schema(), ensure_ascii=False, indent=2) in prompt


def test_intake_schema_embeds_the_real_research_request_schema() -> None:
    schema = intake_json_schema()

    assert set(schema["properties"]) == {
        "request",
        "unsupported_criteria",
        "clarification_question",
    }
    criteria = schema["$defs"]["PersonResearchCriteria"]
    assert criteria["additionalProperties"] is False
    assert "rosfinmonitoring_status" in criteria["properties"]
    assert "region" not in criteria["properties"]


def test_parser_sends_prompt_schema_query_and_current_date() -> None:
    parser, client = _parser({"request": {"object_type": "person"}})

    parser.parse("Покажи всех")

    (call,) = client.calls
    assert call["schema_name"] == INTAKE_SCHEMA_NAME
    assert call["system_prompt"] == render_intake_system_prompt()
    assert call["json_schema"] == intake_json_schema()
    assert call["user_message"] == "Current date: 2026-09-14\n\nQuery:\nПокажи всех"


@pytest.mark.parametrize(
    ("scenario", "llm_output", "expected_criteria"),
    [
        (
            "A: политически преследуемые",
            {"request": {"object_type": "person", "criteria": {"persecution_status": "political"}}},
            {"persecution_status": "political"},
        ),
        (
            "B: политически преследуемые, которых нет в РФМ",
            {
                "request": {
                    "object_type": "person",
                    "criteria": {
                        "persecution_status": "political",
                        "rosfinmonitoring_status": "not_matched",
                    },
                }
            },
            {"persecution_status": "political", "rosfinmonitoring_status": "not_matched"},
        ),
        (
            "C: найди Иванова",
            {"request": {"object_type": "person", "criteria": {"name": "Иванов"}}},
            {"name": "Иванов"},
        ),
    ],
)
def test_parser_keeps_structured_request_candidate_as_returned(
    scenario: str, llm_output: dict[str, Any], expected_criteria: dict[str, str]
) -> None:
    parser, _ = _parser(llm_output)

    intake = parser.parse(scenario)

    assert intake.request is not None
    assert intake.request["criteria"] == expected_criteria
    assert intake.unsupported_criteria == []
    assert intake.clarification_question is None


def test_parser_preserves_unsupported_criteria() -> None:
    parser, _ = _parser(
        {
            "request": {"object_type": "person", "criteria": {"persecution_status": "political"}},
            "unsupported_criteria": [
                {"criterion": "occupation", "value": "программисты"},
                {"criterion": "age", "value": "30–35 лет"},
                {"criterion": "region", "value": "из Казани"},
            ],
        }
    )

    intake = parser.parse("политически преследуемые программисты 30–35 лет из Казани")

    assert intake.unsupported_criteria == [
        UnsupportedCriterion(criterion="occupation", value="программисты"),
        UnsupportedCriterion(criterion="age", value="30–35 лет"),
        UnsupportedCriterion(criterion="region", value="из Казани"),
    ]


@pytest.mark.parametrize(
    "llm_output",
    [
        {"answer": "Иванов не в перечне"},
        {"request": "person"},
        {"unsupported_criteria": [{"criterion": "age"}]},
    ],
)
def test_output_not_matching_intake_schema_is_invalid_response(
    llm_output: dict[str, Any],
) -> None:
    parser, _ = _parser(llm_output)

    with pytest.raises(LlmInvalidResponseError):
        parser.parse("что угодно")


def test_domain_invalid_request_is_left_for_validation_node() -> None:
    # Intake does not apply domain rules; validate_request does.
    parser, _ = _parser(
        {
            "request": {
                "object_type": "person",
                "criteria": {"date_from": "2024-02-01", "date_to": "2024-01-01"},
            }
        }
    )

    intake = parser.parse("…")

    assert intake == ResearchIntake(
        request={
            "object_type": "person",
            "criteria": {"date_from": "2024-02-01", "date_to": "2024-01-01"},
        }
    )

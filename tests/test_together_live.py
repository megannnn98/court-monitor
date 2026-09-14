"""Opt-in live test against the real Together AI API (paid, network).

Skipped unless TOGETHER_LIVE_TESTS=1 and TOGETHER_API_KEY / TOGETHER_MODEL are
set, so a plain `uv run pytest` never calls the external API even when a key
happens to be present in the environment.
"""

from __future__ import annotations

import os

import pytest

from candidate_query_models import RosfinmonitoringStatus
from persecution_models import PersecutionClassificationStatus
from research_models import ResearchRequest
from research_workflow.intake import LlmResearchRequestParser
from together_llm_client import TogetherConfig, TogetherStructuredLlmClient

pytestmark = [
    pytest.mark.live_together,
    pytest.mark.skipif(
        os.environ.get("TOGETHER_LIVE_TESTS") != "1",
        reason="set TOGETHER_LIVE_TESTS=1 (plus TOGETHER_API_KEY, TOGETHER_MODEL) to call Together AI",
    ),
]


@pytest.fixture(scope="module")
def parser() -> LlmResearchRequestParser:
    return LlmResearchRequestParser(TogetherStructuredLlmClient(TogetherConfig.from_env()))


def test_live_political_query_does_not_add_rosfinmonitoring_status(
    parser: LlmResearchRequestParser,
) -> None:
    intake = parser.parse("Найди политически преследуемых людей.")

    request = ResearchRequest.model_validate(intake.request)
    assert request.criteria.persecution_status is PersecutionClassificationStatus.POLITICAL
    assert request.criteria.rosfinmonitoring_status is None
    assert intake.unsupported_criteria == []


def test_live_anti_war_not_in_list_query(parser: LlmResearchRequestParser) -> None:
    intake = parser.parse(
        "Найди людей, которых преследовали за антивоенную деятельность "
        "и которых нет в перечне Росфинмониторинга."
    )

    assert intake.request is not None
    criteria = intake.request["criteria"]
    assert criteria["persecution_status"] == PersecutionClassificationStatus.POLITICAL.value
    assert criteria["rosfinmonitoring_status"] == RosfinmonitoringStatus.NOT_MATCHED.value
    assert criteria.get("snapshot_id") is None


def test_live_unsupported_criteria_are_reported(parser: LlmResearchRequestParser) -> None:
    intake = parser.parse("Найди политически преследуемых программистов 30–35 лет из Казани.")

    assert len(intake.unsupported_criteria) >= 3

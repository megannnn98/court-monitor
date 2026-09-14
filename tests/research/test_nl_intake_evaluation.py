"""Natural-language intake evaluation: query → LLM output (fake or live) → executed ResearchRequest.

The fake LLM returns the recorded output; the test checks what the deterministic
workflow makes of it (validation, snapshot defaulting, unsupported criteria,
clarification). With LIVE_LLM_TESTS=1 (or TOGETHER_LIVE_TESTS=1) and Together
AI configured, the same queries go to the real model; that accuracy is printed
and never gates deterministic product correctness.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from support.research_workflow_fakes import (
    FakeResearchService,
    FakeSnapshotLookup,
    FakeStructuredLlmClient,
)

from research.planning.planner import ResearchPlanner
from research.workflow.graph import build_research_graph, run_research_query
from research.workflow.intake import LlmResearchRequestParser
from research.workflow.llm import StructuredLlmClient
from research.workflow.models import ResearchQueryResult, RosfinmonitoringSnapshotSummary
from sources.source_registry import SOURCES

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "final_evaluation" / "nl_intake.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = DATA["cases"]


def _run(client: StructuredLlmClient, query: str) -> ResearchQueryResult:
    graph = build_research_graph(
        request_parser=LlmResearchRequestParser(client),
        research_service=FakeResearchService(),
        snapshot_lookup=FakeSnapshotLookup(
            latest=RosfinmonitoringSnapshotSummary(
                snapshot_id=DATA["latest_snapshot_id"],
                snapshot_date=datetime(2026, 9, 1, tzinfo=UTC),
                entry_count=100,
                match_count=10,
            )
        ),
        planner=ResearchPlanner(SOURCES),
    )
    return run_research_query(graph, query)


def _criteria(result: ResearchQueryResult) -> dict[str, Any]:
    assert result.request is not None
    return result.request.criteria.model_dump(mode="json", exclude_none=True)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_intake_fixture_produces_the_expected_request(case: dict[str, Any]) -> None:
    result = _run(FakeStructuredLlmClient(data=case["llm_output"]), case["query"])

    accepted = case.get("expected_status_any_of") or [case["expected_status"]]
    assert result.status.value in accepted, (result.status, result.error)
    if "expected_criteria" in case:
        assert _criteria(result) == case["expected_criteria"]
    if "expected_unsupported" in case:
        assert [item.criterion for item in result.unsupported_criteria] == case[
            "expected_unsupported"
        ]


LIVE = os.environ.get("LIVE_LLM_TESTS") == "1" or os.environ.get("TOGETHER_LIVE_TESTS") == "1"


@pytest.mark.live_together
@pytest.mark.skipif(not LIVE, reason="set LIVE_LLM_TESTS=1 with TOGETHER_API_KEY/TOGETHER_MODEL")
def test_live_llm_intake_accuracy_is_reported() -> None:
    from llm.together_client import TogetherConfig, TogetherStructuredLlmClient

    client = TogetherStructuredLlmClient(TogetherConfig.from_env())
    correct = 0
    for case in CASES:
        result = _run(client, case["query"])
        accepted = case.get("expected_status_any_of") or [case["expected_status"]]
        ok = result.status.value in accepted and (
            "expected_criteria" not in case
            or (result.request is not None and _criteria(result) == case["expected_criteria"])
        )
        correct += ok
        print(f"{'OK ' if ok else 'BAD'} {case['id']}: {result.status.value}")
    print(f"live intake accuracy: {correct}/{len(CASES)}")

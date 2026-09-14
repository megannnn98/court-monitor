"""In-memory fakes for research workflow tests (no network, no database)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from research.models import ResearchRequest, ResearchResponse
from research.workflow.llm import LlmError, LlmUsage, StructuredLlmResult
from research.workflow.models import ResearchIntake, RosfinmonitoringSnapshotSummary


@dataclass
class FakeStructuredLlmClient:
    """Returns a canned JSON object (or raises) and records the call."""

    data: dict[str, Any] | None = None
    error: LlmError | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_message: str,
        schema_name: str,
        json_schema: dict[str, Any],
    ) -> StructuredLlmResult:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_message": user_message,
                "schema_name": schema_name,
                "json_schema": json_schema,
            }
        )
        if self.error is not None:
            raise self.error
        assert self.data is not None
        return StructuredLlmResult(
            data=self.data, model="fake-model", usage=LlmUsage(prompt_tokens=10)
        )


@dataclass
class FakeRequestParser:
    intake: ResearchIntake | None = None
    error: LlmError | None = None
    queries: list[str] = field(default_factory=list)

    def parse(self, text: str) -> ResearchIntake:
        self.queries.append(text)
        if self.error is not None:
            raise self.error
        assert self.intake is not None
        return self.intake


@dataclass
class FakeResearchService:
    response: ResearchResponse | None = None
    error: Exception | None = None
    requests: list[ResearchRequest] = field(default_factory=list)

    candidate_calls: list[list[int] | None] = field(default_factory=list)

    def execute(
        self,
        request: ResearchRequest,
        *,
        candidate_person_ids: Sequence[int] | None = None,
    ) -> ResearchResponse:
        self.requests.append(request)
        self.candidate_calls.append(
            None if candidate_person_ids is None else list(candidate_person_ids)
        )
        if self.error is not None:
            raise self.error
        if self.response is not None:
            return self.response
        return ResearchResponse(object_type=request.object_type, request=request, total_matched=0)


@dataclass
class FakeSnapshotLookup:
    latest: RosfinmonitoringSnapshotSummary | None = None
    calls: int = 0

    def latest_imported_snapshot(self) -> RosfinmonitoringSnapshotSummary | None:
        self.calls += 1
        return self.latest

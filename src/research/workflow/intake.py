"""Request intake: natural language -> ResearchIntake."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from string import Template
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

from candidates.models import RosfinmonitoringStatus
from extraction.models import EventType
from persecution.models import PersecutionClassificationStatus
from research.models import MAX_RESEARCH_LIMIT, ResearchObjectType, ResearchRequest
from research.workflow.llm import LlmInvalidResponseError, StructuredLlmClient
from research.workflow.models import ResearchIntake, UnsupportedCriterion
from sources.source_registry import SOURCES

INTAKE_SCHEMA_NAME = "research_intake"
_PROMPT_PATH = Path(__file__).parent / "prompts" / "request_intake.md"

logger = logging.getLogger("research_workflow")


class ResearchRequestParser(Protocol):
    def parse(self, text: str) -> ResearchIntake:
        """Extract a structured request candidate from natural language.

        Raises `LlmError` on provider failure or unusable output.
        """
        ...


class _IntakeSchema(BaseModel):
    """Schema-only mirror of ResearchIntake with the request typed, so the
    LLM receives the real ResearchRequest JSON schema."""

    request: ResearchRequest | None = None
    unsupported_criteria: list[UnsupportedCriterion] = Field(default_factory=list)
    clarification_question: str | None = None


def intake_json_schema() -> dict[str, Any]:
    return _IntakeSchema.model_json_schema()


def render_intake_system_prompt() -> str:
    def values(items: list[str]) -> str:
        return ", ".join(f'"{item}"' for item in items)

    return Template(_PROMPT_PATH.read_text(encoding="utf-8")).substitute(
        object_types=values([t.value for t in ResearchObjectType]),
        persecution_statuses=values([s.value for s in PersecutionClassificationStatus]),
        rosfinmonitoring_statuses=values([s.value for s in RosfinmonitoringStatus]),
        event_types=values([t.value for t in EventType]),
        sources=values([definition.source_name for definition in SOURCES.values()]),
        max_limit=MAX_RESEARCH_LIMIT,
        json_schema=json.dumps(intake_json_schema(), ensure_ascii=False, indent=2),
    )


def _today_utc() -> date:
    return datetime.now(UTC).date()


class LlmResearchRequestParser:
    def __init__(
        self,
        client: StructuredLlmClient,
        *,
        today: Callable[[], date] = _today_utc,
    ) -> None:
        self._client = client
        self._today = today
        self._system_prompt = render_intake_system_prompt()
        self._json_schema = intake_json_schema()

    def parse(self, text: str) -> ResearchIntake:
        result = self._client.complete_json(
            system_prompt=self._system_prompt,
            user_message=f"Current date: {self._today().isoformat()}\n\nQuery:\n{text}",
            schema_name=INTAKE_SCHEMA_NAME,
            json_schema=self._json_schema,
        )
        # Token usage only; never the prompt, query or credentials.
        logger.info(
            "llm_intake_completed model=%s prompt_tokens=%s completion_tokens=%s",
            result.model,
            result.usage.prompt_tokens,
            result.usage.completion_tokens,
        )
        try:
            return ResearchIntake.model_validate(result.data)
        except ValidationError as exc:
            raise LlmInvalidResponseError(
                f"LLM output does not match the intake schema: {exc.error_count()} error(s)"
            ) from exc


class PreparedRequestParser:
    """Intake for callers that already hold a structured request (MCP, evaluation).

    The request still goes through the whole deterministic workflow: validation,
    snapshot defaulting, planning, research, review policy and the report. No
    language model is involved.
    """

    def __init__(self, request: dict[str, Any]) -> None:
        self._request = dict(request)

    def parse(self, text: str) -> ResearchIntake:
        return ResearchIntake(request=dict(self._request))

"""Provider-agnostic structured LLM client contract."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class LlmError(Exception):
    """Base class for LLM provider failures. Never means "no results"."""


class LlmConfigurationError(LlmError):
    """The provider is not configured (e.g. missing API key or model)."""


class LlmTimeoutError(LlmError):
    pass


class LlmUnavailableError(LlmError):
    """Network failure or provider-side (5xx) error."""


class LlmAuthenticationError(LlmError):
    pass


class LlmRateLimitError(LlmError):
    pass


class LlmRequestRejectedError(LlmError):
    """Provider rejected the request itself (4xx other than auth/rate limit)."""


class LlmInvalidResponseError(LlmError):
    """Provider answered, but not with a usable structured result."""


class LlmUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class StructuredLlmResult(BaseModel):
    """Parsed JSON object returned by the model. Not yet domain-validated."""

    data: dict[str, Any]
    model: str | None = None
    usage: LlmUsage = Field(default_factory=LlmUsage)


class StructuredLlmClient(Protocol):
    def complete_json(
        self,
        *,
        system_prompt: str,
        user_message: str,
        schema_name: str,
        json_schema: dict[str, Any],
    ) -> StructuredLlmResult:
        """Return a JSON object constrained by `json_schema`.

        Raises an `LlmError` subclass on any provider failure, including
        output that is not a JSON object.
        """
        ...

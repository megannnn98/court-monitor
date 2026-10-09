"""Together AI structured-output client (infrastructure boundary).

Only this module knows about Together's HTTP API: its errors, which answers it
accepts and what it reports; the OpenAI-compatible request and envelope are
`monitor_core.llm`'s. The AI review of ER decisions depends on
`llm.structured.StructuredLlmClient`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import ValidationError

from llm.structured import (
    LlmAuthenticationError,
    LlmConfigurationError,
    LlmInvalidResponseError,
    LlmRateLimitError,
    LlmRequestRejectedError,
    LlmTimeoutError,
    LlmUnavailableError,
    LlmUsage,
    StructuredLlmResult,
)
from monitor_core.llm import CHAT_ENVELOPE_ERRORS, post_json_chat, read_chat_completion

TOGETHER_BASE_URL = "https://api.together.ai/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class TogetherConfig:
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    base_url: str = TOGETHER_BASE_URL

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TogetherConfig:
        env = os.environ if env is None else env
        api_key = env.get("TOGETHER_API_KEY", "").strip()
        model = env.get("TOGETHER_MODEL", "").strip()
        missing = [
            name
            for name, value in [("TOGETHER_API_KEY", api_key), ("TOGETHER_MODEL", model)]
            if not value
        ]
        if missing:
            raise LlmConfigurationError(f"Together AI is not configured: set {', '.join(missing)}")
        raw_timeout = env.get("TOGETHER_TIMEOUT_SECONDS", "").strip()
        try:
            timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_SECONDS
        except ValueError:
            raise LlmConfigurationError("TOGETHER_TIMEOUT_SECONDS must be a number") from None
        if timeout <= 0:
            raise LlmConfigurationError("TOGETHER_TIMEOUT_SECONDS must be positive")
        # Any OpenAI-compatible server with JSON schema output, e.g. a local Ollama
        # (http://127.0.0.1:11434/v1); the API key is then an arbitrary placeholder.
        base_url = env.get("TOGETHER_BASE_URL", "").strip().rstrip("/") or TOGETHER_BASE_URL
        if not base_url.startswith(("http://", "https://")):
            raise LlmConfigurationError("TOGETHER_BASE_URL must start with http:// or https://")
        return cls(api_key=api_key, model=model, timeout_seconds=timeout, base_url=base_url)


class TogetherStructuredLlmClient:
    def __init__(self, config: TogetherConfig, *, http_client: httpx.Client | None = None) -> None:
        self._config = config
        self._http_client = http_client or httpx.Client()

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_message: str,
        schema_name: str,
        json_schema: dict[str, Any],
    ) -> StructuredLlmResult:
        try:
            response = post_json_chat(
                self._http_client,
                f"{self._config.base_url}/chat/completions",
                model=self._config.model,
                system=system_prompt,
                user=user_message,
                schema_name=schema_name,
                schema=json_schema,
                headers={"Authorization": f"Bearer {self._config.api_key}"},
                timeout_seconds=self._config.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise LlmTimeoutError(
                f"Together AI request timed out after {self._config.timeout_seconds}s"
            ) from exc
        except httpx.TransportError as exc:
            raise LlmUnavailableError(f"Together AI is unreachable: {type(exc).__name__}") from exc

        self._raise_for_status(response)
        return self._parse_response(response)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        # The error body is not included: it may echo request content.
        message = f"Together AI returned HTTP {status}"
        if status in (401, 403):
            raise LlmAuthenticationError(message)
        if status == 429:
            raise LlmRateLimitError(message)
        if status >= 500:
            raise LlmUnavailableError(message)
        raise LlmRequestRejectedError(message)

    def _parse_response(self, response: httpx.Response) -> StructuredLlmResult:
        try:
            completion = read_chat_completion(response)
        except CHAT_ENVELOPE_ERRORS as exc:
            raise LlmInvalidResponseError("Together AI response has unexpected shape") from exc

        if completion.finish_reason == "length":
            raise LlmInvalidResponseError("Together AI output was truncated (finish_reason=length)")
        content = completion.content
        if not isinstance(content, str):
            raise LlmInvalidResponseError("Together AI returned no text content")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LlmInvalidResponseError("Together AI output is not valid JSON") from exc
        if not isinstance(data, dict):
            raise LlmInvalidResponseError("Together AI output is not a JSON object")

        try:
            usage = LlmUsage.model_validate(completion.usage)
        except ValidationError:
            # Usage is diagnostic only; a malformed block must not fail intake.
            usage = LlmUsage()
        return StructuredLlmResult(
            data=data,
            model=completion.model if isinstance(completion.model, str) else self._config.model,
            usage=usage,
        )

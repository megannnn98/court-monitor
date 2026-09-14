"""Together AI structured-output client (infrastructure boundary).

Only this module knows about Together's HTTP API. The workflow depends on
`research_workflow.llm.StructuredLlmClient`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import ValidationError

from research.workflow.llm import (
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
        return cls(api_key=api_key, model=model, timeout_seconds=timeout)


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
        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": json_schema},
            },
            "temperature": 0,
        }
        try:
            response = self._http_client.post(
                f"{self._config.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._config.api_key}"},
                timeout=self._config.timeout_seconds,
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
            body = response.json()
            choice = body["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlmInvalidResponseError("Together AI response has unexpected shape") from exc

        if choice.get("finish_reason") == "length":
            raise LlmInvalidResponseError("Together AI output was truncated (finish_reason=length)")
        if not isinstance(content, str):
            raise LlmInvalidResponseError("Together AI returned no text content")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LlmInvalidResponseError("Together AI output is not valid JSON") from exc
        if not isinstance(data, dict):
            raise LlmInvalidResponseError("Together AI output is not a JSON object")

        try:
            usage = LlmUsage.model_validate(body.get("usage") or {})
        except ValidationError:
            # Usage is diagnostic only; a malformed block must not fail intake.
            usage = LlmUsage()
        return StructuredLlmResult(
            data=data,
            model=body.get("model") if isinstance(body.get("model"), str) else self._config.model,
            usage=usage,
        )

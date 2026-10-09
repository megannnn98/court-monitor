"""One OpenAI-compatible chat completion asked for JSON: a single HTTP call, no retry.

What the answer means, what it may cost a run and how it is logged belong to the
caller; this module only sends the request and reads the provider's envelope.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx


class ModelError(Exception):
    pass


@dataclass(frozen=True)
class Endpoint:
    provider: str
    url: str
    model: str
    api_key: str | None = None


@dataclass(frozen=True)
class ChatCompletion:
    """The first choice of an answer, as the provider sent it."""

    content: object
    finish_reason: object
    usage: Mapping[str, Any]

    @property
    def cost_usd(self) -> float:
        """What the provider says the call cost (OpenRouter does); 0 when it does not."""
        return float(self.usage.get("cost") or 0.0)

    def text(self) -> str:
        """The answer's text; a `ModelError` when it was cut or holds none."""
        # Checked first: a cut answer is also broken JSON, and the cut is the reason.
        if self.finish_reason not in (None, "stop"):
            raise ModelError(f"unusable answer: finish_reason={self.finish_reason}")
        if not isinstance(self.content, str):
            raise ModelError("unusable answer: no content")
        return self.content


def request_json_chat(
    http: httpx.Client,
    endpoint: Endpoint,
    *,
    system: str,
    user: str,
    schema_name: str,
    schema: dict[str, object],
    max_tokens: int,
    timeout_seconds: float,
) -> ChatCompletion:
    """Ask for an answer strict to `schema`, no reasoning, temperature 0.

    The network, an HTTP error or an envelope without a first choice is a `ModelError`;
    a cut or empty answer is returned, and refused by `ChatCompletion.text`."""
    body: dict[str, Any] = {
        "model": endpoint.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if endpoint.provider == "openrouter":
        # Only providers that honour the schema; no chain of thought eating max_tokens;
        # and the price of the call, to count.
        body["provider"] = {"require_parameters": True}
        body["reasoning"] = {"enabled": False}
        body["usage"] = {"include": True}
    headers = {"Authorization": f"Bearer {endpoint.api_key}"} if endpoint.api_key else {}
    try:
        response = http.post(endpoint.url, json=body, headers=headers, timeout=timeout_seconds)
    except httpx.HTTPError as exc:
        raise ModelError(f"{type(exc).__name__}: {exc}") from exc
    if response.status_code >= 400:
        # The provider's message says why (credit, model, schema); it holds no secret.
        raise ModelError(f"HTTP {response.status_code}: {response.text[:300]}")
    try:
        payload = response.json()
        choice = payload["choices"][0]
        content = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ModelError(f"unusable answer: {type(exc).__name__}") from exc
    return ChatCompletion(
        content=content,
        finish_reason=choice.get("finish_reason"),
        usage=payload.get("usage") or {},
    )

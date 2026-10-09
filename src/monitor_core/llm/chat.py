"""The OpenAI-compatible wire protocol of a chat completion asked for JSON.

`post_json_chat` sends the request; `read_chat_completion` reads the provider's
envelope. Neither decides what a failure means: httpx errors and any HTTP status reach
the caller as they are, and a broken envelope raises one of `CHAT_ENVELOPE_ERRORS`.
Which answers to accept, how to name a failure and whether to show the provider's
message are each caller's own policy, as are its provider's extra options.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

# What reading a malformed envelope raises: not JSON, no first choice, no message.
CHAT_ENVELOPE_ERRORS = (ValueError, KeyError, IndexError, TypeError)


@dataclass(frozen=True)
class ChatCompletion:
    """The first choice of an answer, as the provider sent it."""

    content: object
    finish_reason: object
    usage: Mapping[str, Any]
    model: object


def post_json_chat(
    http: httpx.Client,
    url: str,
    *,
    model: str,
    system: str,
    user: str,
    schema_name: str,
    schema: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout_seconds: float,
    strict: bool = False,
    extra_body: Mapping[str, Any] | None = None,
) -> httpx.Response:
    """A system and a user message, an answer constrained to `schema`, temperature 0.

    `extra_body` follows the standard fields in its own order (`max_tokens`, a
    provider's options)."""
    json_schema: dict[str, Any] = {"name": schema_name}
    if strict:
        json_schema["strict"] = True
    json_schema["schema"] = schema
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": json_schema},
        "temperature": 0,
        **(extra_body or {}),
    }
    return http.post(url, json=body, headers=dict(headers), timeout=timeout_seconds)


def read_chat_completion(response: httpx.Response) -> ChatCompletion:
    """The first choice of a 2xx answer; one of `CHAT_ENVELOPE_ERRORS` when there is none."""
    payload = response.json()
    choice = payload["choices"][0]
    content = choice["message"]["content"]
    return ChatCompletion(
        content=content,
        finish_reason=choice.get("finish_reason"),
        usage=payload.get("usage") or {},
        model=payload.get("model"),
    )

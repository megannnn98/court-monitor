"""The model an investigation may ask, behind one small port.

A model here reads and words; it is never a source of a fact. It is optional: without
one the graph runs on rules alone. Tests use a fake of the port; nothing in the graph
knows a provider.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from llm.chat import Endpoint, Spend, chat_json, endpoint_from_env

PROMPTS = Path(__file__).parents[1] / "prompts"


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class ModelReply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class InvestigationModel(Protocol):
    def ask(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, object],
        max_tokens: int,
    ) -> ModelReply:
        """A JSON answer strict to `schema`. `llm.chat.ModelError` for any failure."""
        ...


class ChatInvestigationModel:
    """The model the entity steps use (`llm.chat`): OpenRouter, strict JSON."""

    def __init__(self, endpoint: Endpoint, *, http_client: httpx.Client | None = None) -> None:
        self._endpoint = endpoint
        # None: one client per call, closed with it; a caller's own client is its to close.
        self._http = http_client

    def ask(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, object],
        max_tokens: int,
    ) -> ModelReply:
        spend = Spend()

        def call(http: httpx.Client) -> str:
            return chat_json(
                http,
                self._endpoint,
                system=system,
                user=user,
                schema_name=schema_name,
                schema=schema,
                max_tokens=max_tokens,
                spend=spend,
            )

        if self._http is not None:
            text = call(self._http)
        else:
            with httpx.Client() as http:
                text = call(http)
        return ModelReply(
            text=text,
            input_tokens=spend.prompt_tokens,
            output_tokens=spend.completion_tokens,
            cost_usd=spend.cost_usd,
        )


def investigation_model_from_env(env: Mapping[str, str] | None = None) -> InvestigationModel | None:
    """None without OpenRouter: the investigation then runs on rules alone."""
    endpoint = endpoint_from_env(env)
    return None if endpoint is None else ChatInvestigationModel(endpoint)

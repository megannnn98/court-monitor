"""One way to ask a model for JSON, for every entity step: where, at what cost, how much.

- `Endpoint`: OpenRouter, DeepSeek by default (`ENTITY_NORMALIZE_MODEL` names another).
- `Spend`: what a step's calls cost (OpenRouter reports each call's price) and the
  budget of one run (`ENTITY_MODEL_BUDGET_USD`): past it, nothing more is asked; the
  rest stays unasked, as if the model failed, and the next run asks it.
- `ask_in_batches`: the batches in parallel, never more than the budget allows.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

import httpx

from monitor_core.llm import Endpoint, ModelError, request_json_chat

logger = logging.getLogger("entities")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "deepseek/deepseek-v4.1-flash"
TIMEOUT_SECONDS = 180.0
# A run of one step spends at most this much unless the environment says otherwise.
DEFAULT_BUDGET_USD = 2.0


class BudgetExceededError(ModelError):
    """The run's budget is spent: the batch was not asked."""


def endpoint_from_env(env: Mapping[str, str] | None = None) -> Endpoint | None:
    """OpenRouter when its key is set; else None: the steps then keep what the rules
    give. (A local qwen2.5-7b was tried: it called 63 of 108 political cases «unclear»;
    the user dropped it.)"""
    env = os.environ if env is None else env
    model = env.get("ENTITY_NORMALIZE_MODEL", "").strip()
    key = env.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return None
    return Endpoint(
        provider="openrouter", url=OPENROUTER_URL, model=model or OPENROUTER_MODEL, api_key=key
    )


def budget_from_env(env: Mapping[str, str] | None = None) -> float:
    env = os.environ if env is None else env
    try:
        return float(env.get("ENTITY_MODEL_BUDGET_USD", "") or DEFAULT_BUDGET_USD)
    except ValueError:
        return DEFAULT_BUDGET_USD


class Spend:
    """What one run's calls cost, from threads; whether its budget still allows a call."""

    def __init__(self, budget_usd: float = DEFAULT_BUDGET_USD) -> None:
        self.budget_usd = budget_usd
        self._cost = 0.0
        self._calls = 0
        self._lock = threading.Lock()

    def add(self, cost: float) -> None:
        with self._lock:
            self._cost += cost
            self._calls += 1

    @property
    def cost_usd(self) -> float:
        with self._lock:
            return self._cost

    @property
    def calls(self) -> int:
        with self._lock:
            return self._calls

    def exhausted(self) -> bool:
        return self.cost_usd >= self.budget_usd


def chat_json(
    http: httpx.Client,
    endpoint: Endpoint,
    *,
    system: str,
    user: str,
    schema_name: str,
    schema: dict[str, object],
    max_tokens: int,
    spend: Spend | None = None,
) -> str:
    """The model's JSON answer as text: strict to `schema`, no reasoning, temperature 0.

    Every failure — the network, the provider, a cut answer — is a `ModelError`."""
    completion = request_json_chat(
        http,
        endpoint,
        system=system,
        user=user,
        schema_name=schema_name,
        schema=schema,
        max_tokens=max_tokens,
        timeout_seconds=TIMEOUT_SECONDS,
    )
    cost = completion.cost_usd
    if spend is not None:
        spend.add(cost)
    logger.info(
        "event=entity_model_call provider=%s model=%s prompt_tokens=%s completion_tokens=%s "
        "cost_usd=%.6f",
        endpoint.provider,
        endpoint.model,
        completion.usage.get("prompt_tokens"),
        completion.usage.get("completion_tokens"),
        cost,
    )
    # A cut answer is counted and logged first: it was paid for.
    return completion.text()


def ask_in_batches[Batch, Answer](
    batches: Iterable[Batch],
    ask: Callable[[Batch], Answer],
    *,
    concurrency: int,
    spend: Spend | None = None,
) -> Iterator[tuple[Batch, Answer | Exception]]:
    """Each batch with its answer or its error, as they come; at most `concurrency` in
    flight. Once the budget is spent no batch is sent: each left is yielded with a
    `BudgetExceededError`."""
    queue = iter(batches)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        pending: dict[Future[Answer], Batch] = {}

        def fill() -> None:
            while len(pending) < concurrency and not (spend is not None and spend.exhausted()):
                batch = next(queue, None)
                if batch is None:
                    return
                pending[pool.submit(ask, batch)] = batch

        fill()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                batch = pending.pop(future)
                try:
                    yield batch, future.result()
                except Exception as exc:  # noqa: BLE001 - handed to the caller, per batch
                    yield batch, exc
            fill()
    for batch in queue:
        yield batch, BudgetExceededError("the run's budget is spent")

"""One way to ask a model for JSON, for every entity step: where, at what cost, how much.

- `Endpoint`: OpenRouter, DeepSeek by default (`ENTITY_NORMALIZE_MODEL` names another).
- `Spend`: what a step's calls cost (OpenRouter reports each call's price) and the
  budget of one run (`ENTITY_MODEL_BUDGET_USD`): past it, nothing more is asked; the
  rest stays unasked, as if the model failed, and the next run asks it.
- `ask_in_batches`: the batches in parallel, never more than the budget allows.
- `LEDGER`: what a step's process spent per model, for `entity_model_spend`; it counts
  only once a step command turns it on (`cli.cycle`), so the API's own calls stay out.

A `:free` model that reports a price, or an answer from another model than the one
asked, is a failure: the choice of the model is the operator's, never swapped silently.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any

import httpx

from monitor_core.llm import CHAT_ENVELOPE_ERRORS, post_json_chat, read_chat_completion

logger = logging.getLogger("entities")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "deepseek/deepseek-v4.1-flash"
TIMEOUT_SECONDS = 180.0
# A run of one step spends at most this much unless the environment says otherwise.
DEFAULT_BUDGET_USD = 2.0


class ModelError(Exception):
    pass


class ModelTimeoutError(ModelError):
    """The model did not answer in time."""


class ModelHttpError(ModelError):
    """The provider answered an HTTP error: 429 is a rate limit (free models have tight ones)."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class BudgetExceededError(ModelError):
    """The run's budget is spent: the batch was not asked."""


@dataclass(frozen=True)
class Endpoint:
    provider: str
    url: str
    model: str
    api_key: str | None = None


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


@dataclass
class ModelTally:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    # Calls OpenRouter told no cost for: the console estimates them by the price list.
    uncosted_calls: int = 0
    uncosted_prompt_tokens: int = 0
    uncosted_completion_tokens: int = 0


class Ledger:
    """Every call of a process, per model; off until a step command turns it on."""

    def __init__(self) -> None:
        self.active = False
        self._tallies: dict[str, ModelTally] = {}
        self._lock = threading.Lock()

    def add(
        self, model: str, cost: float | None, *, prompt_tokens: int, completion_tokens: int
    ) -> None:
        if not self.active:
            return
        with self._lock:
            tally = self._tallies.setdefault(model, ModelTally())
            tally.calls += 1
            tally.prompt_tokens += prompt_tokens
            tally.completion_tokens += completion_tokens
            if cost is None:
                tally.uncosted_calls += 1
                tally.uncosted_prompt_tokens += prompt_tokens
                tally.uncosted_completion_tokens += completion_tokens
            else:
                tally.cost_usd += cost

    def tallies(self) -> dict[str, ModelTally]:
        with self._lock:
            return {model: ModelTally(**vars(tally)) for model, tally in self._tallies.items()}

    def reset(self) -> None:
        with self._lock:
            self._tallies.clear()


LEDGER = Ledger()


def is_free_model(model: str) -> bool:
    """OpenRouter's free variants carry the `:free` suffix in their id."""
    return model.endswith(":free")


def served_by_asked(asked: str, served: object) -> bool:
    """Whether the answer came from the model asked. OpenRouter answers the dated slug
    (`deepseek/deepseek-v4.1-flash-20260910` for `deepseek/deepseek-v4.1-flash`) and may
    keep or drop the `:free` suffix; no model named in the answer is no evidence."""
    if not isinstance(served, str) or not served:
        return True
    base = asked.removesuffix(":free")
    return served.removesuffix(":free").startswith(base)


class Spend:
    """What one run's calls cost, from threads; whether its budget still allows a call."""

    def __init__(self, budget_usd: float = DEFAULT_BUDGET_USD) -> None:
        self.budget_usd = budget_usd
        self._cost = 0.0
        self._calls = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._uncosted_calls = 0
        self._lock = threading.Lock()

    def add(
        self,
        cost: float,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        reported: bool = True,
    ) -> None:
        with self._lock:
            self._cost += cost
            self._calls += 1
            self._uncosted_calls += not reported
            self._prompt_tokens += prompt_tokens
            self._completion_tokens += completion_tokens

    @property
    def prompt_tokens(self) -> int:
        with self._lock:
            return self._prompt_tokens

    @property
    def completion_tokens(self) -> int:
        with self._lock:
            return self._completion_tokens

    @property
    def uncosted_calls(self) -> int:
        """Calls OpenRouter told no cost for: `cost_usd` leaves them out."""
        with self._lock:
            return self._uncosted_calls

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

    def stop(self) -> None:
        """No more calls this run: as if its budget were spent."""
        self.budget_usd = 0.0


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
    timeout_seconds: float = TIMEOUT_SECONDS,
) -> str:
    """The model's JSON answer as text: strict to `schema`, no reasoning, temperature 0.

    Every failure — the network, the provider, a cut answer — is a `ModelError`."""
    extra: dict[str, Any] = {"max_tokens": max_tokens}
    if endpoint.provider == "openrouter":
        # Only providers that honour the schema; no chain of thought eating max_tokens;
        # and the price of the call, to count.
        extra["provider"] = {"require_parameters": True}
        extra["reasoning"] = {"enabled": False}
        extra["usage"] = {"include": True}
    headers = {"Authorization": f"Bearer {endpoint.api_key}"} if endpoint.api_key else {}
    try:
        response = post_json_chat(
            http,
            endpoint.url,
            model=endpoint.model,
            system=system,
            user=user,
            schema_name=schema_name,
            schema=schema,
            headers=headers,
            timeout_seconds=timeout_seconds,
            strict=True,
            extra_body=extra,
        )
    except httpx.TimeoutException as exc:
        raise ModelTimeoutError(f"{type(exc).__name__}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise ModelError(f"{type(exc).__name__}: {exc}") from exc
    if response.status_code >= 400:
        # The provider's message says why (credit, model, schema); it holds no secret.
        raise ModelHttpError(
            response.status_code, f"HTTP {response.status_code}: {response.text[:300]}"
        )
    try:
        completion = read_chat_completion(response)
    except CHAT_ENVELOPE_ERRORS as exc:
        raise ModelError(f"unusable answer: {type(exc).__name__}") from exc
    usage = completion.usage
    reported = usage.get("cost")
    cost = float(reported or 0.0)
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    if spend is not None:
        spend.add(
            cost,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reported=reported is not None,
        )
    LEDGER.add(
        endpoint.model,
        None if reported is None else cost,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    logger.info(
        "event=entity_model_call provider=%s model=%s served_model=%s prompt_tokens=%s "
        "completion_tokens=%s cost_usd=%.6f",
        endpoint.provider,
        endpoint.model,
        completion.model,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        cost,
    )
    if endpoint.provider == "openrouter":
        if not served_by_asked(endpoint.model, completion.model):
            logger.error(
                "event=entity_model_swapped asked=%s served=%s", endpoint.model, completion.model
            )
            raise ModelError(f"answered by another model: {completion.model}")
        if is_free_model(endpoint.model) and cost > 0:
            logger.error(
                "event=entity_free_model_billed model=%s cost_usd=%.6f", endpoint.model, cost
            )
            # A free model that bills will bill every batch: nothing more is asked.
            if spend is not None:
                spend.stop()
            raise ModelError(f"the free model {endpoint.model} was billed ${cost:.6f}")
    # Checked first: a cut answer is also broken JSON, and the cut is the reason.
    if completion.finish_reason not in (None, "stop"):
        raise ModelError(f"unusable answer: finish_reason={completion.finish_reason}")
    if not isinstance(completion.content, str):
        raise ModelError("unusable answer: no content")
    return completion.content


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

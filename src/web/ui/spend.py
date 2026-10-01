"""What the steps that ask a model cost, and what is left to spend.

Four steps of the cycle ask a paid model through OpenRouter: the purge (Jev, per article),
the entities (names to the nominative), the figurants and the political step (DeepSeek). The
operator is told so before a step starts, with what the account can still pay for, and again
on the step's card while it runs.

The balance comes from OpenRouter's `/api/v1/credits` (credits bought minus credits used).
`/api/v1/key` is not it: that is the key's own limit, which can read «89 left» on an account
with four dollars. The answer is cached for a minute and asked with a short timeout, so a
page does not wait on OpenRouter and a dead OpenRouter does not slow every page: when it
cannot be asked, the page says so and nothing else changes.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html import escape

import httpx

from entities.llm import budget_from_env, endpoint_from_env

CREDITS_URL = "https://openrouter.ai/api/v1/credits"
CACHE_SECONDS = 60.0
TIMEOUT_SECONDS = 4.0
# The steps that ask a model, in the order of the cycle.
AI_STAGES = ("purge", "entities", "figurants", "political")
# What one article costs the purge's decision model: $0.0391 for 682 articles (measured).
PURGE_COST_PER_ARTICLE = 0.00006

_lock = threading.Lock()
_cache: tuple[str, float, Balance | None] | None = None


@dataclass(frozen=True)
class Balance:
    bought: float
    used: float

    @property
    def remaining(self) -> float:
        return self.bought - self.used


def _request(api_key: str) -> object:
    """OpenRouter's answer for the account's credits, or None when it cannot be had."""
    try:
        response = httpx.get(
            CREDITS_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=TIMEOUT_SECONDS
        )
        return response.json() if response.status_code == httpx.codes.OK else None
    except (httpx.HTTPError, ValueError):
        return None


def _parse(answer: object) -> Balance | None:
    try:
        data = answer["data"]  # type: ignore[index]
        return Balance(float(data["total_credits"]), float(data["total_usage"]))
    except (KeyError, TypeError, ValueError):
        return None


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def balance(
    env: Mapping[str, str] | None = None, now: Callable[[], float] = time.monotonic
) -> Balance | None:
    """The account's balance, or None: no key, or OpenRouter did not answer."""
    global _cache
    key = (os.environ if env is None else env).get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return None
    with _lock:
        if _cache is not None and _cache[0] == key and now() - _cache[1] < CACHE_SECONDS:
            return _cache[2]
    found = _parse(_request(key))
    with _lock:
        _cache = (key, now(), found)
    return found


def paid_deepseek(env: Mapping[str, str] | None = None) -> bool:
    """The steps that use DeepSeek would be paid: OpenRouter, a DeepSeek model, not `:free`."""
    endpoint = endpoint_from_env(env)
    return (
        endpoint is not None
        and endpoint.provider == "openrouter"
        and "deepseek" in endpoint.model.lower()
        and ":free" not in endpoint.model.lower()
    )


def paid(stage: str, env: Mapping[str, str] | None = None) -> bool:
    """Whether this step would spend money now."""
    env = os.environ if env is None else env
    if stage == "purge":
        return env.get("JUNK_SCREEN", "").strip().lower() == "jev" and bool(
            env.get("OPENROUTER_API_KEY", "").strip()
        )
    return stage in AI_STAGES and paid_deepseek(env)


def spend_text(stage: str, env: Mapping[str, str] | None = None) -> str:
    """What the step costs, in words; empty when it costs nothing."""
    if not paid(stage, env):
        return ""
    if stage == "purge":
        return (
            "Шаг платный: каждую статью на удаление оценивает модель JEV через OpenRouter, "
            f"около ${PURGE_COST_PER_ARTICLE:.5f} за статью."
        )
    return (
        f"Шаг платный: DeepSeek через OpenRouter, не более ${budget_from_env(env):.2f} за запуск."
    )


def balance_text(env: Mapping[str, str] | None = None) -> str:
    """The balance as a sentence; empty with no key at all."""
    key = (os.environ if env is None else env).get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return ""
    found = balance(env)
    if found is None:
        return "Баланс OpenRouter сейчас узнать не удалось."
    return (
        f"Остаток на OpenRouter: ${found.remaining:.2f} "
        f"(куплено ${found.bought:.2f}, потрачено ${found.used:.2f})."
    )


def is_low(stage: str, env: Mapping[str, str] | None = None) -> bool:
    """The account cannot pay for a whole run of this step: its limit is above the balance."""
    found = balance(env)
    return (
        found is not None
        and paid(stage, env)
        and stage != "purge"
        and found.remaining < budget_from_env(env)
    )


def confirm_text(stage: str, env: Mapping[str, str] | None = None) -> str:
    """The balance for the browser's confirmation: only where the step is paid."""
    return balance_text(env) if paid(stage, env) else ""


def notice(stage: str, env: Mapping[str, str] | None = None) -> str:
    """The visible warning of a paid step: the cost, the balance, and a red word when the
    balance is under the step's limit. Empty for a step that is free."""
    cost = spend_text(stage, env)
    if not cost:
        return ""
    low = is_low(stage, env)
    extra = " Остатка меньше лимита запуска: шаг может остановиться, не закончив." if low else ""
    css = "warning" if low else "muted"
    return (
        f'<p class="{css} ai-note">⚠ {escape(cost)} {escape(balance_text(env))}{escape(extra)}</p>'
    )


def balance_line(env: Mapping[str, str] | None = None) -> str:
    """The balance alone, for the page of the cycle."""
    text = balance_text(env)
    return f'<p class="muted ai-note">{escape(text)}</p>' if text else ""

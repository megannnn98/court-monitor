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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from html import escape

import httpx

from entities.llm import budget_from_env, endpoint_from_env
from monitoring.junk_screen import DECISION

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


def _api_key(env: Mapping[str, str]) -> str:
    return env.get("OPENROUTER_API_KEY", "").strip()


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
    key = _api_key(os.environ if env is None else env)
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


@dataclass(frozen=True)
class StageCost:
    """What a paid step costs: the words for the operator, and what one run may spend at
    most. No limit for a step whose whole run costs cents — its price is no reason to
    alarm over the balance."""

    text: str
    limit: float | None = None


def cost(stage: str, env: Mapping[str, str] | None = None) -> StageCost | None:
    """What this step would spend now; None for a step that is free."""
    env = os.environ if env is None else env
    if stage == "purge":
        if env.get("JUNK_SCREEN", "").strip().lower() != DECISION or not _api_key(env):
            return None
        return StageCost(
            "Шаг платный: каждую статью на удаление оценивает модель JEV через OpenRouter, "
            f"около ${PURGE_COST_PER_ARTICLE:.5f} за статью."
        )
    if stage not in AI_STAGES or not paid_deepseek(env):
        return None
    limit = budget_from_env(env)
    return StageCost(
        f"Шаг платный: DeepSeek через OpenRouter, не более ${limit:.2f} за запуск.", limit
    )


def paid(stage: str, env: Mapping[str, str] | None = None) -> bool:
    """Whether this step would spend money now."""
    return cost(stage, env) is not None


def spend_text(stage: str, env: Mapping[str, str] | None = None) -> str:
    """What the step costs, in words; empty when it costs nothing."""
    found = cost(stage, env)
    return found.text if found else ""


def cost_text(stage: str, env: Mapping[str, str] | None = None) -> str:
    """The words of what a step costs; empty for a step that is free."""
    found = cost(stage, env)
    return found.text if found else ""


def balance_text(env: Mapping[str, str] | None = None) -> str:
    """The balance as a sentence; empty with no key at all."""
    if not _api_key(os.environ if env is None else env):
        return ""
    found = balance(env)
    if found is None:
        return "Баланс OpenRouter сейчас узнать не удалось."
    return (
        f"Остаток на OpenRouter: ${found.remaining:.2f} "
        f"(куплено ${found.bought:.2f}, потрачено ${found.used:.2f})."
    )


def _is_low(limit: float | None, env: Mapping[str, str] | None) -> bool:
    """The account cannot pay for a whole run: its limit is above the balance."""
    found = balance(env)
    return found is not None and limit is not None and found.remaining < limit


def chain_shortfall(stages: Sequence[str], env: Mapping[str, str] | None = None) -> str:
    """«Сделать всё» runs several paid steps: a warning when the account cannot pay for
    every one of them to its limit. Empty when it can, or when the balance is unknown."""
    total = sum(found.limit or 0 for stage in stages if (found := cost(stage, env)))
    if not total or not _is_low(total, env):
        return ""
    return (
        f"Остатка меньше суммы лимитов этих шагов (${total:.2f}): цепочка может "
        "остановиться, не дойдя до конца."
    )


def confirm_text(stage: str, env: Mapping[str, str] | None = None) -> str:
    """What the browser's confirmation says of the money: the cost and the balance, only
    where the step is paid."""
    found = cost(stage, env)
    return " ".join(part for part in (found.text, balance_text(env)) if part) if found else ""


def notice(stage: str, env: Mapping[str, str] | None = None) -> str:
    """The visible warning of a paid step: the cost, the balance, and a red word when the
    balance is under the step's limit. Empty for a step that is free."""
    found = cost(stage, env)
    if found is None:
        return ""
    low = _is_low(found.limit, env)
    extra = " Остатка меньше лимита запуска: шаг может остановиться, не закончив." if low else ""
    css = "warning" if low else "muted"
    return (
        f'<p class="{css} ai-note">⚠ {escape(found.text)} {escape(balance_text(env))}'
        f"{escape(extra)}</p>"
    )


def balance_line(env: Mapping[str, str] | None = None) -> str:
    """The balance alone, for the page of the cycle."""
    text = balance_text(env)
    return f'<p class="muted ai-note">{escape(text)}</p>' if text else ""


def strip_item(env: Mapping[str, str] | None = None) -> str:
    """The balance for the strip at the top of every page: a figure to see, not to look for.

    Nothing with no key at all (the console then spends nothing). A dash when OpenRouter
    cannot be asked; red when the balance is under what one run of a step may spend."""
    key = _api_key(os.environ if env is None else env)
    if not key:
        return ""
    found = balance(env)
    label = "<small>Баланс OpenRouter</small>"
    if found is None:
        return (
            '<span class="balance unknown" title="OpenRouter сейчас не ответил">'
            f"{label}<strong>—</strong></span>"
        )
    limit = budget_from_env(env)
    low = _is_low(limit, env)
    title = (
        f"Куплено ${found.bought:.2f}, потрачено ${found.used:.2f}. "
        f"Лимит одного запуска — ${limit:.2f}."
        + (" Остатка меньше: шаг может остановиться, не закончив." if low else "")
    )
    return (
        f'<span class="balance{" low" if low else ""}" title="{escape(title, quote=True)}">'
        f"{label}<strong>${found.remaining:.2f}</strong></span>"
    )

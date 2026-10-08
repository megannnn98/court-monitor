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

What a run spent is kept with the run: every paid step prints the cost of its model calls
among its totals (`…cost_usd`), and the journal of runs keeps what a run printed. So the
history of spending is the history of runs: `run_cost` sums a run's costs, `last_spent`
finds what the step cost the time before. A run that did not reach its totals — stopped,
failed — printed none: its cost is not known, and is said so, not shown as zero. Nor did
a step that ended well before it began to count its cost (the purge, until 07.10.2026):
that run's cost is «не записан».
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from html import escape

import httpx

from entities.llm import budget_from_env, endpoint_from_env
from monitoring.junk_screen import DECISION
from operator_console import OperationRun, OperationRunStatus

CREDITS_URL = "https://openrouter.ai/api/v1/credits"
CACHE_SECONDS = 60.0
TIMEOUT_SECONDS = 4.0
# The steps that ask a model, in the order of the cycle.
AI_STAGES = ("purge", "entities", "figurants", "political")
# What one article costs the purge's decision model: $0.0391 for 682 articles (measured).
PURGE_COST_PER_ARTICLE = 0.00006
# What every paid step together costs for one day of news (the operator's estimate).
COST_PER_NEWS_DAY = 0.07

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
    costs = [found for stage in stages if (found := cost(stage, env))]
    total = sum(found.limit or 0 for found in costs)
    if not total or not _is_low(total, env):
        return ""
    # A step paid by the piece has no limit: it is not in the sum, and says so.
    beyond = (
        " Шаг без лимита (оплата за каждую статью) в эту сумму не входит."
        if any(found.limit is None for found in costs)
        else ""
    )
    return (
        f"Остатка меньше суммы лимитов этих шагов (${total:.2f}): цепочка может "
        f"остановиться, не дойдя до конца.{beyond}"
    )


def chain_cost_text(stages: Sequence[str], env: Mapping[str, str] | None = None) -> str:
    """The question before «Сделать всё» when a step is paid: the money, and nothing else."""
    found = balance(env)
    left = f"Остаток: ${found.remaining:.2f}." if found else "Остаток узнать не удалось."
    short = " Остатка может не хватить на все шаги." if chain_shortfall(stages, env) else ""
    return (
        "С баланса OpenRouter спишутся деньги: примерно "
        f"${COST_PER_NEWS_DAY:.2f} за один день новостей. {left}{short} Запустить?"
    )


def confirm_text(stage: str, env: Mapping[str, str] | None = None) -> str:
    """What the browser's confirmation says of the money: the cost and the balance, only
    where the step is paid."""
    found = cost(stage, env)
    return " ".join(part for part in (found.text, balance_text(env)) if part) if found else ""


def run_cost(run: OperationRun) -> float | None:
    """What the run's model calls cost: the sum of the costs it printed at its end. None
    when it printed none — a step that asks no model, or a run that did not end."""
    try:
        totals = json.loads(run.stdout) if run.stdout else {}
    except json.JSONDecodeError:
        return None
    if not isinstance(totals, dict):
        return None
    costs = [
        value
        for key, value in totals.items()
        if key.endswith("cost_usd") and isinstance(value, int | float) and value is not True
    ]
    return round(sum(costs), 6) if costs else None


def money(cost: float) -> str:
    """Cents are what a run costs: four places, so that a run of half a cent is not «$0.00»."""
    return f"${cost:.4f}"


def previous(
    runs: Sequence[OperationRun], stage: str | None, before: int | None = None
) -> tuple[OperationRun, float] | None:
    """The latest run of the step whose cost is known, of `runs` (newest first); with
    `before`, the latest one older than that run."""
    for run in runs:
        if run.parameters.mode != stage or (before is not None and run.id >= before):
            continue
        cost = run_cost(run)
        if cost is not None:
            return run, cost
    return None


def _when(run: OperationRun) -> str:
    return f"#{run.id}, {run.created_at.astimezone():%d.%m.%Y %H:%M}"


def last_spent(runs: Sequence[OperationRun], stage: str | None) -> str:
    """«В прошлый раз …» for a step about to start; empty when no run of it told a cost."""
    found = previous(runs, stage)
    if found is None:
        return ""
    run, cost = found
    return f"В прошлый раз шаг потратил {money(cost)} ({_when(run)})."


_LIVE = (OperationRunStatus.PENDING, OperationRunStatus.RUNNING)


def unknown_cost(run: OperationRun) -> str:
    """Why an ended run of a paid step has no cost, in a word: «не записан» for one that
    ended well (the step did not count its cost then), «неизвестно» for one that did not
    reach its totals. Empty for a live run or a free step."""
    if run.parameters.mode not in AI_STAGES or run.status in _LIVE:
        return ""
    return "не записан" if run.status is OperationRunStatus.SUCCEEDED else "неизвестно"


def run_spent(run: OperationRun, runs: Sequence[OperationRun]) -> str:
    """Under a run's card: what this run spent, and what the step spent the time before.
    Empty for a step that asks no model."""
    if run.parameters.mode not in AI_STAGES:
        return ""
    cost = run_cost(run)
    parts = []
    if cost is not None:
        parts.append(f"Этот запуск потратил на модель {money(cost)}.")
    elif unknown_cost(run) == "неизвестно":
        parts.append("Сколько потратил этот запуск, неизвестно: он не дошёл до итогов.")
    elif unknown_cost(run):
        parts.append("Расход этого запуска не записан: тогда шаг его ещё не считал.")
    before = previous(runs, run.parameters.mode, run.id)
    if before is not None:
        parts.append(f"Предыдущий запуск этого шага ({_when(before[0])}): {money(before[1])}.")
    return f'<p class="muted run-spent">{escape(" ".join(parts))}</p>' if parts else ""


def notice(stage: str, env: Mapping[str, str] | None = None, *, last: str = "") -> str:
    """The visible warning of a paid step: the cost, the balance, what the step spent the
    time before (`last`), and a red word when the balance is under the step's limit. Empty
    for a step that is free."""
    found = cost(stage, env)
    if found is None:
        return ""
    low = _is_low(found.limit, env)
    extra = " Остатка меньше лимита запуска: шаг может остановиться, не закончив." if low else ""
    css = "warning" if low else "muted"
    return (
        f'<p class="{css} ai-note">⚠ {escape(found.text)} {escape(balance_text(env))}'
        f"{escape(' ' + last if last else '')}{escape(extra)}</p>"
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

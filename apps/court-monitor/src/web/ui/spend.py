"""What the steps that ask a model cost, and what is left to spend.

Four steps of the cycle ask a model through OpenRouter: the purge (Jev, per article), the
entities (names to the nominative), the figurants and the political step (the entity model
chosen on «Модель извлечения», DeepSeek by default, `llm.model_choice`). The operator is
told so before a step starts — the entity model by its id, its price per million tokens
and whether it is free — with what the account can still pay for, and again on the step's
card while it runs.

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

from collections.abc import Mapping, Sequence
from html import escape

from orchestrator.operations import OperationRun
from web.views.spend import (
    AI_STAGES,
    _is_low,
    _when,
    balance_text,
    cost,
    money,
    previous,
    run_cost,
    strip_balance,
    unknown_cost,
)


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


def notice(
    stage: str,
    env: Mapping[str, str] | None = None,
    *,
    last: str = "",
    model: str | None = None,
    decision_model: str | None = None,
) -> str:
    """The visible warning of a paid step: the cost, the balance, what the step spent the
    time before (`last`), and a red word when the balance is under the step's limit. Empty
    for a step that is free."""
    found = cost(stage, env, model=model, decision_model=decision_model)
    if found is None:
        return ""
    if not found.paid:
        return f'<p class="muted ai-note">{escape(found.text)}</p>'
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
    """`strip_balance` as the legacy strip's chip; empty with no key."""
    found = strip_balance(env)
    if found is None:
        return ""
    css = "balance" + (" low" if found.low else "") + ("" if found.known else " unknown")
    return (
        f'<span class="{css}" title="{escape(found.hint, quote=True)}">'
        f"<small>Баланс OpenRouter</small><strong>{found.figure}</strong></span>"
    )

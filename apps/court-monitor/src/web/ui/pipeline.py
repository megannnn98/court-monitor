"""The manual pipeline: five steps in a cycle, one button that can be pressed at a time.

1. load → 2. purge → 3. entities → 4. figurants → 5. political + Rosfinmonitoring → back to 1. Person resolution has no button for now
(the user's call); a legacy resolution run started outside this UI ends a
cycle. The step that may run now comes from the latest monitor run alone:
- a live run is the current step (its button stops it);
- a run stopped or lost (interrupted) is repeated;
- a purge or an entity rebuild that failed crashed, and is repeated too; a load
  «failed» when one of many sources did — the step is done, its errors are in its card,
  and one broken source must not block the cycle;
- otherwise the next step. No run at all: the first.

«Сделать всё» runs the steps from the current one to the last by one press: each step is
still a run of its own, and a chained step that ends well starts the next
(`orchestrator.operations.OperationRegistry._continue_chain`). What the page says of a chain —
the question before it, the note under a live step, the word when it stopped — is here.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape
from urllib.parse import urlencode

from web.entity_model import current_entity_model
from web.ui import spend
from web.views.pipeline import (
    _ACTIONS,
    _WITH_SOURCES,
    HINTS,
    STAGES,
    TITLES,
    PipelineState,
    _stage_of,
    chain_confirmation,
    chain_note_text,
    chain_span,
    chain_stopped_text,
    latest_run_id,
    step_confirmation,
    title_of,
)
from web.views.spend import STAGE_KINDS, entity_model_notice


def models_query(stages: Sequence[str]) -> str:
    """The models the warnings of `stages` name, for the start's address (`em`, `dm`):
    the server refuses a start when one of them changed since the page was drawn, so a
    step never asks a model its warning did not show. Only the models those steps ask
    (`spend.STAGE_KINDS`): another model's change is no reason to refuse. Empty without
    OpenRouter, or for steps that ask none."""
    if entity_model_notice() is None:
        return ""
    kinds = {kind for stage in stages for kind in STAGE_KINDS.get(stage, ())}
    params = {
        name: current_entity_model(kind)
        for name, kind in (("em", "extraction"), ("dm", "decision"))
        if kind in kinds
    }
    return urlencode(params).replace("&", "&amp;") if params else ""


def chain_action(state: PipelineState) -> str:
    """The address that starts the current step and, after it, every step to the end of
    the cycle. `after` is the latest run the page was drawn for: the server starts a
    chain only while that is still the latest, so the same press cannot start a second
    chain — a form sent again from the browser's history, after the cycle went round,
    would otherwise delete and spend with nobody asked."""
    models = models_query(STAGES[STAGES.index(state.current) :])
    return f"{_ACTIONS[state.current]}?chain=1&amp;after={latest_run_id(state)}" + (
        f"&amp;{models}" if models else ""
    )


def ask(question: str) -> str:
    """The attributes of a button that asks before it submits. The question is kept as
    data and read by the handler: written into the handler's own text, an apostrophe in
    it would break the script — and a button whose script is broken submits unasked."""
    if not question:
        return ""
    return f' data-ask="{escape(question, quote=True)}" onclick="return confirm(this.dataset.ask)"'


def chain_stopped(state: PipelineState) -> str:
    """Why «Сделать всё» did not reach the last step, said where the button stands: a
    chain that stopped leaves the page looking as if the steps had been pressed one by one."""
    text = chain_stopped_text(state)
    return f'<p class="warning chain-stopped">{escape(text)}</p>' if text else ""


def chain_note(state: PipelineState) -> str:
    """Under a live chained step: what happens when it ends."""
    text = chain_note_text(state)
    return f'<p class="muted chain-note">{escape(text)}</p>' if text else ""


def stepper(state: PipelineState, checked_count: int, *, back: str = "management") -> str:
    """The six buttons joined by arrows; only the current one can be pressed.

    Buttons submit the page's source form (`formaction`), so the step with sources
    carry the selection; the selection script only touches `.run-button`."""
    current = STAGES.index(state.current)
    # Only the current step's button can be pressed: its models alone.
    models = models_query((state.current,))
    query = f"?{models}" if models else ""
    # Step 1 counts the sources it loads; the selection script updates it.
    counts = {"load": f' (<span class="selected-count">{checked_count}</span>)'}
    steps: list[str] = []
    for index, stage in enumerate(STAGES):
        label = f"{index + 1}. {TITLES[stage]}{counts.get(stage, '')}"
        hint = escape(HINTS[stage], quote=True)
        if stage == state.current and state.live is not None:
            button = (
                f'<button id="stop-button" class="step danger" type="submit" '
                f'formaction="/ui/management/runs/{state.live.id}/stop" name="back" '
                f'value="{back}" title="{hint}" '
                "onclick=\"return confirm('Остановить запуск? Уже сделанное останется.')\">"
                f"■ Остановить: {escape(title_of(state.live))}</button>"
            )
        elif stage == state.current:
            needs_sources = stage in _WITH_SOURCES
            warning = step_confirmation(stage)
            confirm = ask(warning)
            classes = "step current" + (" run-button" if needs_sources else "")
            disabled = " disabled" if needs_sources and not checked_count else ""
            button = (
                f'<button id="step-{stage}" class="{classes}" type="submit" '
                f'formaction="{_ACTIONS[stage]}{query}" title="{hint}"{confirm}{disabled}>'
                f"{label}</button>"
            )
        else:
            done = index < current
            why = "уже сделано в этом круге" if done else "ещё не очередь"
            button = (
                f'<button class="step {"done" if done else "later"}" type="button" disabled '
                f'title="{hint} — {why}">{"✓ " if done else ""}{label}</button>'
            )
        steps.append(button)
    arrows = '<span class="step-arrow" aria-hidden="true">→</span>'
    everything = ""
    if state.live is None:
        needs_sources = state.current in _WITH_SOURCES
        everything = (
            f"{chain_stopped(state)}"
            f'<p class="pipeline-all"><button id="do-all" class="primary-action'
            f'{" run-button" if needs_sources else ""}" type="submit" '
            f'formaction="{chain_action(state)}"'
            f"{ask(chain_confirmation(state.current))}"
            f"{' disabled' if needs_sources and not checked_count else ''}>Сделать всё</button> "
            f'<span class="muted">{chain_span(state.current)}, без остановок; '
            "кнопки ниже запускают по одному шагу</span></p>"
        )
    if state.live is None:
        running = f"Следующий шаг {current + 1}: {escape(HINTS[state.current])}."
    elif _stage_of(state.live) in STAGES:
        running = f"Идёт шаг {current + 1}: {escape(TITLES[state.current])}."
    else:
        running = f"Идёт {escape(title_of(state.live).lower())}."
    return (
        f"{everything}"
        f'<div class="pipeline">{arrows.join(steps)}</div>'
        f"{chain_note(state)}"
        f'<p class="muted pipeline-note">{running} После шага {len(STAGES)} круг начинается '
        "заново.</p>"
        f"{spend.notice(state.current, last=state.last_spent) if state.live is None else ''}"
    )

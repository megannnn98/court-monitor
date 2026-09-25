"""The manual pipeline: three steps in a cycle, one button that can be pressed at a time.

1. load → 2. purge → 3. entities → back to 1. Person resolution has no button for now
(the user's call); a resolution run, from before or from the bot's /update, ends a
cycle. The step that may run now comes from the latest monitor run alone:
- a live run is the current step (its button stops it);
- a run stopped or lost (interrupted) is repeated;
- a purge or an entity rebuild that failed crashed, and is repeated too; a load
  «failed» when one of many sources did — the step is done, its errors are in its card,
  and one broken source must not block the cycle;
- otherwise the next step. No run at all: the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from operator_console import OperationRegistry, OperationRun, OperationRunStatus

OPERATION = "monitor"
STAGES = ("load", "purge", "entities")
TITLES = {
    "load": "Подгрузить статьи",
    "purge": "Очистить от мусора",
    "entities": "Собрать сущности",
    # No button; names a resolution run that is still going.
    "resolve": "Разрешение персон",
}
HINTS = {
    "load": "скачать новые публикации выбранных источников и извлечь людей и события",
    "purge": "удалить статьи без уголовных дел и людей, которых ничто больше не упоминает",
    "entities": "собрать людей из упоминаний и привести имена к именительному падежу",
}
_ACTIONS = {
    "load": "/ui/management/run",
    "purge": "/ui/management/purge",
    "entities": "/ui/management/entities",
}
# Steps that take the source selection; the others cover the whole database.
_WITH_SOURCES = frozenset({"load"})
_CONFIRM = {
    "purge": "Удалить из базы все статьи без уголовных дел и людей, которых после этого "
    "ничто не упоминает? Это необратимо.",
}
_LIVE = (OperationRunStatus.PENDING, OperationRunStatus.RUNNING)


@dataclass(frozen=True)
class PipelineState:
    current: str
    live: OperationRun | None = None


def _stage_of(run: OperationRun) -> str:
    """The run's step; a resolution (mode "resolve", or the bot's full cycle) is none."""
    mode = run.parameters.mode
    return mode if mode in STAGES else "resolve"


def pipeline_state(latest: OperationRun | None) -> PipelineState:
    if latest is None:
        return PipelineState(current=STAGES[0])
    stage = _stage_of(latest)
    if stage not in STAGES:
        # A resolution ends the cycle; while it runs, its stop button stands on step 1.
        live = latest if latest.status in _LIVE else None
        return PipelineState(current=STAGES[0], live=live)
    if latest.status in _LIVE:
        return PipelineState(current=stage, live=latest)
    repeat = latest.status is OperationRunStatus.INTERRUPTED or (
        latest.status is OperationRunStatus.FAILED and stage not in _WITH_SOURCES
    )
    if repeat:
        return PipelineState(current=stage)
    return PipelineState(current=STAGES[(STAGES.index(stage) + 1) % len(STAGES)])


def current_state(registry: OperationRegistry) -> PipelineState:
    latest = registry.runs_of(OPERATION, limit=1)
    return pipeline_state(latest[0] if latest else None)


def out_of_turn(state: PipelineState, stage: str) -> str | None:
    """Why `stage` may not start now, or None when it is its turn."""
    if state.live is not None:
        return (
            f"Идёт запуск #{state.live.id} ({TITLES[_stage_of(state.live)]}): "
            "дождитесь его или остановите."
        )
    if stage != state.current:
        number = STAGES.index(state.current) + 1
        return f"Сейчас шаг {number}: «{TITLES[state.current]}». Шаги идут по порядку."
    return None


def stepper(state: PipelineState, checked_count: int, *, back: str = "management") -> str:
    """The three buttons joined by arrows; only the current one can be pressed.

    Buttons submit the page's source form (`formaction`), so the steps with sources
    carry the selection; the selection script only touches `.run-button`."""
    current = STAGES.index(state.current)
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
                f"■ Остановить: {escape(TITLES[_stage_of(state.live)])}</button>"
            )
        elif stage == state.current:
            needs_sources = stage in _WITH_SOURCES
            confirm = (
                f" onclick=\"return confirm('{_CONFIRM[stage]}')\"" if stage in _CONFIRM else ""
            )
            classes = "step current" + (" run-button" if needs_sources else "")
            disabled = " disabled" if needs_sources and not checked_count else ""
            button = (
                f'<button id="step-{stage}" class="{classes}" type="submit" '
                f'formaction="{_ACTIONS[stage]}" title="{hint}"{confirm}{disabled}>'
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
    if state.live is None:
        running = f"Следующий шаг {current + 1}: {escape(HINTS[state.current])}."
    elif _stage_of(state.live) in STAGES:
        running = f"Идёт шаг {current + 1}: {escape(TITLES[state.current])}."
    else:
        running = f"Идёт {escape(TITLES[_stage_of(state.live)].lower())}."
    return (
        f'<div class="pipeline">{arrows.join(steps)}</div>'
        f'<p class="muted pipeline-note">{running} После шага {len(STAGES)} круг начинается '
        "заново.</p>"
    )

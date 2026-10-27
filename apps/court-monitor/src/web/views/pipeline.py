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

from dataclasses import dataclass, replace

from orchestrator.operations import OperationRegistry, OperationRun, OperationRunStatus
from web.views import spend

OPERATION = "monitor"

STAGES = ("load", "purge", "entities", "figurants", "political")

TITLES = {
    "load": "Подгрузить статьи",
    "purge": "Очистить от мусора",
    "entities": "Собрать сущности",
    "figurants": "Найти фигурантов",
    "political": "Отобрать политические дела и сверить с РФМ",
    # No button; names a resolution run that is still going.
    "resolve": "Разрешение персон",
    # No button either: the check is inside the final step now; names an old run.
    "rosfin": "Сверка с РФМ (прежний отдельный шаг)",
}

HINTS = {
    "load": "скачать новые публикации выбранных источников и извлечь людей и события",
    "purge": "удалить статьи без уголовных дел и людей, которых ничто больше не упоминает",
    "entities": "собрать людей из упоминаний и привести имена к именительному падежу",
    "figurants": "понять, на кого из оставшихся заведено дело, а кто только упомянут",
    "political": "отделить политическое преследование от уголовщины и сверить людей с РФМ — это «Результат»",
}

_ACTIONS = {
    "load": "/ui/management/run",
    "purge": "/ui/management/purge",
    "entities": "/ui/management/entities",
    "figurants": "/ui/management/figurants",
    "political": "/ui/management/political",
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
    # The latest run, live or ended: what a chain that stopped is told by.
    latest: OperationRun | None = None
    # «В прошлый раз шаг потратил …» of the current step; empty when no run told.
    last_spent: str = ""


def _stage_of(run: OperationRun) -> str:
    """The run's step; a legacy resolution run (mode ``resolve``) has none."""
    mode = run.parameters.mode
    # `rosfin` was a standalone step before the five-step cycle. Keep old runs visible;
    # after one, the new cycle continues with figurants.
    return "figurants" if mode == "rosfin" else mode if mode in STAGES else "resolve"


def title_of(run: OperationRun) -> str:
    """What a live run does, by its own mode: an old «rosfin» run is no figurants' search."""
    return TITLES.get(run.parameters.mode or "", TITLES[_stage_of(run)])


def pipeline_state(latest: OperationRun | None) -> PipelineState:
    if latest is None:
        return PipelineState(current=STAGES[0])
    current = pipeline_current(latest.parameters.mode, latest.status)
    live = latest if latest.status in _LIVE else None
    return PipelineState(current=current, live=live, latest=latest)


def pipeline_current(mode: str | None, status: OperationRunStatus | None) -> str:
    """Current step from persisted run fields, usable without an operation registry."""
    if mode is None or mode not in (*STAGES, "rosfin", "resolve"):
        return STAGES[0]
    if mode == "rosfin":
        return "figurants"
    stage = mode if mode in STAGES else "resolve"
    if stage not in STAGES:
        return STAGES[0]
    if status in _LIVE:
        return stage
    repeat = status is OperationRunStatus.INTERRUPTED or (
        status is OperationRunStatus.FAILED and stage not in _WITH_SOURCES
    )
    if repeat:
        return stage
    return STAGES[(STAGES.index(stage) + 1) % len(STAGES)]


# How far back the step's last cost is looked for: four rounds of the five steps.
_SPENT_WINDOW = 20


def current_state(registry: OperationRegistry) -> PipelineState:
    runs = registry.runs_of(OPERATION, limit=_SPENT_WINDOW)
    state = pipeline_state(runs[0] if runs else None)
    return replace(state, last_spent=spend.last_spent(runs, state.current))


def out_of_turn(state: PipelineState, stage: str) -> str | None:
    """Why `stage` may not start now, or None when it is its turn."""
    if state.live is not None:
        return (
            f"Идёт запуск #{state.live.id} ({title_of(state.live)}): дождитесь его или остановите."
        )
    if stage != state.current:
        number = STAGES.index(state.current) + 1
        return f"Сейчас шаг {number}: «{TITLES[state.current]}». Шаги идут по порядку."
    return None


def latest_run_id(state: PipelineState) -> int:
    return state.latest.id if state.latest is not None else 0


def chain_span(stage: str) -> str:
    """«шаги 2–5 подряд», or «шаг 5» when the last alone is left."""
    first, last = STAGES.index(stage) + 1, len(STAGES)
    return f"шаг {last}" if first == last else f"шаги {first}–{last} подряд"


def chain_stopped_text(state: PipelineState) -> str:
    """The words of `chain_stopped`; empty when no chain stopped short."""
    run = state.latest
    if run is None or state.live is not None or not run.parameters.chain:
        return ""
    stage = _stage_of(run)
    if stage not in STAGES:
        return ""
    number = STAGES.index(stage) + 1
    if run.status is OperationRunStatus.SUCCEEDED:
        if stage == STAGES[-1]:
            return ""
        why = "следующий шаг не запустился (сервер перезапускался или шаг запустили вручную)"
    elif run.status is OperationRunStatus.FAILED:
        why = "шаг завершился с ошибкой — посмотрите его карточку в журнале запусков"
    else:
        why = "шаг был остановлен или потерян"
    return (
        f"«Сделать всё» остановилось на шаге {number} "
        f"(запуск #{run.id}): {why}. Кнопка продолжит с шага {STAGES.index(state.current) + 1}."
    )


def chain_confirmation(stage: str, *, with_model: bool = True) -> str:
    """What the browser asks before «Сделать всё»: only the money, when a step left is
    paid. Each step's own warnings stay on its single start in the journal of runs."""
    steps = STAGES[STAGES.index(stage) :]
    # The React console shows the model as a block of its own (`CycleResponse.entity_model`).
    model = spend.chain_model_text(steps) if with_model else ""
    if not any(spend.paid(step) for step in steps):
        return " ".join(part for part in (model, f"Выполнить {chain_span(stage)}?") if part)
    return " ".join(part for part in (model, spend.chain_cost_text(steps)) if part)


def chain_note_text(state: PipelineState) -> str:
    """The words of `chain_note`; empty when no chain is live."""
    if state.live is None or not state.live.parameters.chain:
        return ""
    stage = _stage_of(state.live)
    if stage not in STAGES or stage == STAGES[-1]:
        return "Идёт «Сделать всё»: это последний шаг."
    following = TITLES[STAGES[STAGES.index(stage) + 1]]
    return (
        f"Идёт «Сделать всё»: после этого шага сам запустится «{following}». "
        "«Остановить» прерывает и цепочку."
    )


def step_confirmation(stage: str) -> str:
    """What the browser asks before a step: the destructive warning, then what the step
    costs and what the account can still pay for."""
    return " ".join(part for part in (_CONFIRM.get(stage, ""), spend.confirm_text(stage)) if part)

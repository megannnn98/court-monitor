"""Manual pipeline runs: step 1 loads every news source, steps 2–5 the whole database."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy.orm import Session

from application import build_monitoring_runs
from ingestion.sources.source_registry import news_sources
from llm.chat import Purpose
from llm.model_choice import chosen_model
from orchestrator import read as orchestrator_read
from orchestrator.models import MonitoringRunStatus, MonitoringRunView, MonitoringTrigger
from orchestrator.operations import (
    OperationConflictError,
    OperationParameters,
    OperationRegistry,
    OperationRun,
)
from processing.junk.purge import since_from_env
from web.dependencies import session_factory_for
from web.views import spend
from web.views.pipeline import current_state, latest_run_id, out_of_turn

_OPERATION = "monitor"

# Runs over the whole database: no source selection, a card of their own. `rosfin` stays
# here so old standalone runs remain accessible in the UI.
_WHOLE_DATABASE = ("purge", "entities", "rosfin", "figurants", "political")

HISTORY_SIZE = 4

# What the runs spent is read over several rounds of the five steps.
SPENDING_SIZE = 20


def _has_derived_step(run: OperationRun) -> bool:
    """A load leaves classification to the resolution run; the other runs end with it."""
    return run.parameters.mode not in ("load", *_WHOLE_DATABASE)


def _resolution_progress(item: MonitoringRunView) -> tuple[int, int] | None:
    """(done, total) extraction runs of a source's resolution stage, while it is recorded."""
    metrics = item.stage_metrics.get("resolution")
    if not isinstance(metrics, dict) or "extraction_runs" not in metrics:
        return None
    total = int(metrics["extraction_runs"])
    return int(metrics.get("done", total)), total


# Outcomes of the selected sources of one run: (label, badge colour), in display order.
_OUTCOMES = {
    "completed": ("Готово", "succeeded"),
    "errors": ("С ошибками", "partial"),
    "failed": ("Ошибка", "failed"),
    "running": ("Выполняется", "running"),
    "interrupted": ("Прервано", "failed"),
    "busy": ("Заняты другим запуском", "partial"),
    "waiting": ("Ждут очереди", "pending"),
    "not_started": ("Не запускались", ""),
}

# Number columns of a run's table: (header, what it counts, value of a source's run).
_Column = tuple[str, str, Callable[[MonitoringRunView], int]]

_LOAD_COLUMNS: tuple[_Column, ...] = (
    (
        "Просмотрено",
        "Последние публикации источника, проверенные при поиске новых",
        lambda item: item.documents_discovered,
    ),
    (
        "Уже были",
        "Из просмотренных: уже в базе или без текста — повторно не скачивались",
        lambda item: item.documents_skipped,
    ),
    (
        "Новых",
        "Новые публикации, скачанные и сохранённые в базу",
        lambda item: item.documents_ingested,
    ),
    (
        "Разобрано",
        (
            "Статьи, из которых извлечены люди и события: новые и старые, ещё не "
            "разобранные текущей версией"
        ),
        lambda item: item.articles_extracted,
    ),
    (
        "Событий",
        "Новые события в этих статьях: задержание, обыск, суд, приговор…",
        lambda item: item.events_created,
    ),
)

_RESOLVE_COLUMNS: tuple[_Column, ...] = (
    (
        "Статей",
        "Статьи с упоминаниями людей, ещё не привязанными к человеку, — обработано",
        lambda item: _resolved_articles(item),
    ),
    (
        "Новых людей",
        "Упоминания, по которым в базе заведён новый человек",
        lambda item: item.persons_created,
    ),
    (
        "Привязано",
        "Упоминания, привязанные к уже известному человеку",
        lambda item: item.persons_linked,
    ),
    (
        "На проверку",
        "Спорные упоминания: неясно, тот же это человек или другой, — ждут ручной проверки",
        lambda item: item.person_reviews_created,
    ),
)

_ERRORS_COLUMN: _Column = (
    "Ошибок",
    "Публикации или статьи, которые не удалось обработать; причина — в «Сообщении» и в логе",
    lambda item: item.error_count,
)


def _resolved_articles(item: MonitoringRunView) -> int:
    progress = _resolution_progress(item)
    return progress[0] if progress is not None else 0


def _source_outcome(status: MonitoringRunStatus, *, in_progress: bool) -> str:
    """A monitoring run left `running` by a run that ended lost its process: interrupted."""
    if status is MonitoringRunStatus.RUNNING:
        return "running" if in_progress else "interrupted"
    return {
        MonitoringRunStatus.COMPLETED: "completed",
        MonitoringRunStatus.COMPLETED_WITH_ERRORS: "errors",
        MonitoringRunStatus.FAILED: "failed",
        MonitoringRunStatus.ABORTED: "interrupted",
    }[status]


def _spent_cell(run: OperationRun) -> str:
    cost = spend.run_cost(run)
    if cost is not None:
        return spend.money(cost)
    # A paid step that ended without a cost spent something nobody counted.
    return spend.unknown_cost(run) or "—"


def _skipped_sources(run: OperationRun) -> dict[str, str]:
    try:
        results = json.loads(run.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(results, list):
        return {}
    return {
        str(item["source"]): str(item["skipped"])
        for item in results
        if isinstance(item, dict) and item.get("source") and item.get("skipped")
    }


def _busy_sources(db: Session, run: OperationRun, sources: Sequence[str]) -> set[str]:
    """Sources another monitoring run held when this run began: the CLI skipped them.

    Read from the runs themselves: the CLI's JSON report is cut to its last characters,
    and over dozens of sources the skips at its start are gone."""
    return orchestrator_read.sources_running_at(db, sources, run.started_at or run.created_at)


def _monitoring_runs(db: Session, run: OperationRun) -> list[MonitoringRunView]:
    """The monitoring runs of one manual run: one per source, then the shared derived one."""
    return build_monitoring_runs(session_factory_for(db)).list_runs_started_between(
        run.started_at or run.created_at,
        run.finished_at,
        trigger=MonitoringTrigger.MANUAL,
    )


def _duration(seconds: float) -> str:
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes:02d} мин" if hours else f"{minutes} мин {seconds:02d} с"


SOURCE_ERRORS = 8


def recent_source_errors(db: Session) -> int:
    """The sources that failed to load in the last week."""
    return orchestrator_read.recent_source_error_count(db)


def _recent_runs(registry: OperationRegistry) -> list[OperationRun]:
    """The manual runs with a source selection or over the whole database, newest first:
    the page lists the head of them and reads the spending over the rest."""
    return [
        item
        for item in registry.runs_of(_OPERATION, limit=50)
        if item.parameters.sources is not None or item.parameters.mode in _WHOLE_DATABASE
    ]


class StepRefused(Exception):
    """Why a step may not start, with the HTTP status that says so."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


_MODEL_TITLES: dict[Purpose, str] = {
    "extraction": "Модель извлечения",
    "decision": "Модель решений",
}


def shown_models(
    db: Session, entity_model: str | None, decision_model: str | None
) -> tuple[str | None, str | None]:
    """The models a page's warning showed, checked against the choice now. StepRefused
    (409) when either changed since: the start would ask a model nobody saw. None —
    the page sent none (an old form): the start reads the choice itself."""
    shown: tuple[tuple[Purpose, str | None], ...] = (
        ("extraction", entity_model),
        ("decision", decision_model),
    )
    for kind, model in shown:
        if model is None:
            continue
        chosen = chosen_model(db, kind=kind)
        if chosen != model:
            raise StepRefused(
                f"{_MODEL_TITLES[kind]} сменилась: теперь {chosen}. "
                "Проверьте её цену и запустите снова.",
                409,
            )
    return entity_model, decision_model


_Mode = Literal["load", "purge", "entities", "rosfin", "figurants", "political"]

# A step's name as the run records it; a name not here is no step.
_MODES: dict[str, _Mode] = {
    "load": "load",
    "purge": "purge",
    "entities": "entities",
    "rosfin": "rosfin",
    "figurants": "figurants",
    "political": "political",
}

STALE_CHAIN = (
    "Это нажатие «Сделать всё» устарело: после него уже были запуски. Обновите страницу "
    "и нажмите ещё раз, если шаги по-прежнему нужны."
)


def working_date() -> date | None:
    """The working date (`PIPELINE_SINCE`): step 2 deletes what was published before it."""
    since = since_from_env()
    return since.date() if since is not None else None


def period_refusal(
    published_from: date | None, published_to: date | None, *, today: date | None = None
) -> str | None:
    """Why a load cannot be for these dates; None when it can. A period starts on a date:
    on the working date or after it, or step 2 would delete what was loaded."""
    if published_from is None and published_to is None:
        return None
    if published_from is None:
        return "Для загрузки за период нужна дата «с»; без дат — догрузить всё новое."
    since = working_date()
    if since is not None and published_from < since:
        return (
            f"Дата «с» {published_from:%d.%m.%Y} раньше рабочей даты {since:%d.%m.%Y}: "
            "шаг 2 удалит всё, что опубликовано до неё."
        )
    if published_to is not None and published_from > published_to:
        return "Дата «с» позже даты «по»."
    # A day of slack: the operator's «today» may already be tomorrow in UTC.
    if published_to is not None and published_to > (today or datetime.now(UTC).date()) + timedelta(
        days=1
    ):
        return "Дата «по» в будущем: оставьте её пустой — загрузка по настоящее время."
    return None


def start_step(
    registry: OperationRegistry,
    mode: str,
    *,
    chain: bool,
    after: str | None,
    sources: list[str] | None = None,
    published_from: date | None = None,
    published_to: date | None = None,
    entity_model: str | None = None,
    decision_model: str | None = None,
) -> OperationRun:
    """Start a step of the cycle, or the steps from it to the end (`chain`). Read by the
    legacy forms and `POST /api/v1/cycle/start`. StepRefused when it is not its turn,
    when a chain press is stale (`after` is not the latest run any more), or for a source
    that is not a news source.

    `mode` is a stage of the cycle; any other one is refused as out of turn. `sources` is
    for step 1 only: None there means every news source."""
    state = current_state(registry)
    # A chain press names the latest run its page was drawn for: when runs have happened
    # since, it is an old press sent again, answered for another state of the base.
    if chain and after != str(latest_run_id(state)):
        raise StepRefused(STALE_CHAIN, 409)
    if mode == "load":
        period = period_refusal(published_from, published_to)
        if period is not None:
            raise StepRefused(period, 400)
    # A load for a period is asked for whatever step the round is at: it adds what was
    # published then, and the round goes on from it.
    refusal = out_of_turn(state, mode)
    if refusal is not None and not (
        mode == "load" and published_from is not None and state.live is None
    ):
        raise StepRefused(refusal, 409)
    stage = _MODES.get(mode)
    if stage is None:
        raise StepRefused(f"Неизвестный шаг: {mode}.", 400)
    selected: list[str] | None = None
    if stage == "load":
        everything = [item.name for item in news_sources()]
        selected = list(dict.fromkeys(sources or [])) or everything
        if not set(selected) <= set(everything):
            raise StepRefused("В запросе есть неизвестный или не новостной источник.", 400)
    else:
        # Sources and a period are step 1's; any other step covers the whole database.
        published_from = published_to = None
    parameters = OperationParameters(
        sources=selected,
        mode=stage,
        chain=chain,
        published_from=published_from.isoformat() if published_from else None,
        published_to=published_to.isoformat() if published_to else None,
        entity_model=entity_model,
        decision_model=decision_model,
    )
    try:
        return registry.start(_OPERATION, parameters)
    except OperationConflictError:
        # Another worker started a run between the check and the start.
        raise StepRefused("Идёт другой запуск.", 409) from None

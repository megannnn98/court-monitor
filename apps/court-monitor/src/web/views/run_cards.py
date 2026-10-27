"""The card of a run on «Управление»: one frame for every kind of run, and the cards of
the runs that cover the whole database.

Every card is the same frame — the title with the run's status, when it started, the
progress while it runs, what a paid step costs, the summary, the run's pulse and last log
lines (`web.ui.run_tail`), and the reload while it is live. A kind of run gives only what
is its own: the progress read from its log and the counts it printed at the end.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from orchestrator.operations import OperationRun, OperationRunStatus

RUN_STATUS_LABELS = {
    OperationRunStatus.PENDING: "В очереди",
    OperationRunStatus.RUNNING: "Выполняется",
    OperationRunStatus.SUCCEEDED: "Завершено",
    OperationRunStatus.FAILED: "Завершено с ошибками",
    OperationRunStatus.INTERRUPTED: "Прервано",
}

# Badge colours of `local-ui.css`: yellow while in progress or partly done, green, red.
RUN_STATUS_BADGES = {
    OperationRunStatus.PENDING: "pending",
    OperationRunStatus.RUNNING: "running",
    OperationRunStatus.SUCCEEDED: "succeeded",
    OperationRunStatus.FAILED: "failed",
    OperationRunStatus.INTERRUPTED: "failed",
}


def local_time(moment: datetime) -> str:
    return moment.astimezone().strftime("%d.%m.%Y %H:%M")


MODE_TITLES = {
    "load": "Загрузка статей",
    "resolve": "Разрешение персон",
    "purge": "Очистка от мусора",
    "entities": "Сборка сущностей",
    "rosfin": "Сверка с Росфинмониторингом",
    "figurants": "Поиск фигурантов",
    "political": "Политические дела и сверка с РФМ",
    None: "Загрузка и разрешение",
}

_LIVE = (OperationRunStatus.PENDING, OperationRunStatus.RUNNING)

_PREPARING = "Готовлюсь…"


def in_progress(run: OperationRun) -> bool:
    return run.status in _LIVE


def _last_stage(pattern: re.Pattern[str], stderr: str) -> str:
    """The stage a run logged last (`event=… stage=<stage>`); empty before the first."""
    stages = pattern.findall(stderr)
    return stages[-1].strip() if stages else ""


def _counted(stage: str, verb: str) -> tuple[str, str] | None:
    """«asking 3/10» as (done, total); None for a stage that counts nothing."""
    if not stage.startswith(f"{verb} "):
        return None
    done, _, total = stage.removeprefix(f"{verb} ").partition("/")
    return done, total


def _totals(run: OperationRun) -> dict[str, Any]:
    """The counts a run printed at its end; nothing while it runs or when it printed none."""
    try:
        totals = json.loads(run.stdout) if run.stdout else {}
    except json.JSONDecodeError:
        return {}
    return totals if isinstance(totals, dict) else {}


_Labels = Sequence[tuple[str, str, str]]

_PURGE_PROGRESS = re.compile(
    r"event=junk_purge_progress articles=(\d+) total=(\d+) persons=(\d+) reviews=(\d+)"
    r"(?: outdated=(\d+))?(?: held=(\d+))?"
)

# What a model read of the held (`processing.junk.hold_reader.read_holds` logs its result).
_HOLDS_READ = re.compile(r"event=holds_read HoldsRead\(released=(\d+), model_junk=(\d+)")

_ENTITIES_STAGE = re.compile(r"event=entities_collect_stage stage=([^\n]+)")

_ENTITIES_STAGES = {
    "reading": "Читаю упоминания…",
    "grouping": "Склеиваю…",
    "writing": "Сохраняю…",
}

_ROSFIN_STAGE = re.compile(r"event=entities_rf_check_stage stage=([^\n]+)")

_ROSFIN_STAGES = {
    "downloading": "Скачиваю перечень с fedsfm.ru…",
    "importing": "Перечень изменился — сохраняю новый снимок…",
    "matching": "Сверяю сущности с перечнем…",
    "writing": "Сохраняю…",
    "merging": "Сливаю спорные пары, где человек в перечне…",
}

_FIGURANTS_STAGE = re.compile(r"event=entity_figurants_stage stage=([^\n]+)")

_FIGURANTS_STAGES = {"reading": "Читаю сущности и цитаты…", "writing": "Сохраняю…"}


@dataclass(frozen=True)
class _Phase:
    """A phase of the final step. It logs `event=<event> stage=<reading|asking N/M|writing>`."""

    event: str
    label: str
    # What the model is doing while the phase asks it; empty for a phase that asks none.
    asking: str = ""


# In order: the list, the verdicts, the roundup posts, the latest news, the unnamed,
# the sentences.
_FINAL_PHASES = (
    _Phase("entities_rf_check_stage", "Сверка с перечнем"),
    _Phase("entity_politics_stage", "Политичность", "Модель читает дела"),
    _Phase("article_digest_stage", "Сводки новостей", "Модель отличает сводки от новостей"),
    _Phase("entity_news_stage", "Свежая новость", "Модель определяет, что нового по делу"),
    _Phase("unnamed_stage", "Безымянные", "Модель читает предложения о безымянных"),
    _Phase("sentences_stage", "Приговоры", "Модель выписывает приговоры из публикаций"),
)

_FINAL_STAGE = re.compile(
    r"event=(" + "|".join(phase.event for phase in _FINAL_PHASES) + r") stage=([^\n]+)"
)

_FINAL_RF_STAGES = {
    "downloading": "Скачиваю перечень с fedsfm.ru…",
    "importing": "Перечень изменился — сохраняю новый снимок…",
    "matching": "Сверяю сущности с перечнем…",
    "writing": "Сохраняю сверку с РФМ…",
    "merging": "Сливаю спорные пары по перечню…",
}

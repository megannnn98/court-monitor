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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any

from operator_console import OperationRun, OperationRunStatus
from web.ui import spend
from web.ui.run_tail import tail_html

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


def badge(label: str, css_class: str = "") -> str:
    return f'<span class="badge {css_class}">{escape(label)}</span>'


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


def card(
    run: OperationRun,
    *,
    where: str = "",
    progress: str = "",
    body: str = "",
    status: tuple[str, str] | None = None,
) -> str:
    """The frame of a run's card. `where` stands between «Начат» and the link to the log;
    `progress` is shown while the run is live; `status` replaces the run's own label and
    badge when the card knows better."""
    live = in_progress(run)
    label, css = status or (RUN_STATUS_LABELS[run.status], RUN_STATUS_BADGES[run.status])
    mode = run.parameters.mode
    return f"""<section class="band run-card">
  <h2>Запуск #{run.id} · {MODE_TITLES[mode]} {badge(label, css)}</h2>
  <p class="muted">Начат {escape(local_time(run.created_at))} · {where + " · " if where else ""}<a href="/ui/logs?run_id={run.id}">Лог запуска</a></p>
  {progress if live else ""}
  {spend.notice(mode) if live and mode else ""}
  {body}
  {tail_html(run)}
  {"<script>setTimeout(() => window.location.reload(), 5000);</script>" if live else ""}
</section>"""


def progress_box(words: str, done: str = "", total: str = "") -> str:
    """The words of where a run is; with `done` of `total`, a bar over them."""
    bar = (
        f'<progress class="overall" value="{escape(done)}" max="{escape(total)}"></progress>'
        if total
        else ""
    )
    counted = f": {escape(done)} из {escape(total)}" if total else ""
    return f'<div class="progress-box">{bar}<p><strong>{escape(words)}{counted}</strong></p></div>'


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


def _summary(totals: dict[str, Any], labels: Sequence[tuple[str, str, str]]) -> str:
    """The counts that are not zero, as badges: (key, label, badge) of each."""
    marks = " ".join(
        badge(f"{label}: {totals[key]}", css) for key, label, css in labels if totals.get(key)
    )
    return f'<p class="run-summary">{marks}</p>'


_PURGE_PROGRESS = re.compile(
    r"event=junk_purge_progress articles=(\d+) total=(\d+) persons=(\d+) reviews=(\d+)"
    r"(?: outdated=(\d+))?(?: held=(\d+))?"
)


def _purge_card(run: OperationRun) -> str:
    """A purge has no sources: its card counts what it removed, from its own log."""
    found = _PURGE_PROGRESS.findall(run.stderr)
    # The last progress line holds the running totals; none yet before the first batch.
    articles, total, persons, reviews, outdated, held = (
        int(value or 0) for value in (found[-1] if found else ("0",) * 6)
    )
    progress = (
        f'<div class="progress-box"><progress class="overall" value="{articles}" '
        f'max="{max(total, 1)}"></progress>'
        f"<p><strong>Удалено статей {articles} из {total}</strong></p></div>"
        if found
        else progress_box("Ищу статьи без уголовных дел…")
    )
    summary = " ".join(
        (
            badge(f"Статей удалено: {articles}", "succeeded"),
            badge(f"Из них до рабочей даты: {outdated}", ""),
            badge(f"Людей удалено: {persons}", "succeeded"),
            badge(f"Записей проверки удалено: {reviews}", ""),
            *(
                [
                    f'<a href="/ui/junk-holds">{badge(f"Оставлено на проверку: {held}", "pending")}</a>'
                ]
                if held
                else []
            ),
        )
    )
    return card(
        run,
        where="вся база",
        progress=progress,
        body=f"""<p class="run-summary">{summary}</p>
  <p class="muted">Удаляются статьи, в последнем разборе которых нет уголовного события,
  со всем извлечённым из них, и люди, которых после этого ничто не упоминает.
  Исходная публикация остаётся пустой отметкой, чтобы её не скачивать снова.</p>""",
    )


_ENTITIES_STAGE = re.compile(r"event=entities_collect_stage stage=([^\n]+)")
_ENTITIES_STAGES = {
    "reading": "Читаю упоминания…",
    "grouping": "Склеиваю…",
    "writing": "Сохраняю…",
}


def _entities_card(run: OperationRun) -> str:
    """An entity rebuild: its stage from the log while it runs, its counts at the end."""
    stage = _last_stage(_ENTITIES_STAGE, run.stderr)
    counted = _counted(stage, "normalizing")
    progress = (
        progress_box("Модель приводит имена к именительному", *counted)
        if counted
        else progress_box(_ENTITIES_STAGES.get(stage, _PREPARING))
    )
    labels = (
        ("entities", "Сущностей", "succeeded"),
        ("grouped", "Упоминаний в них", ""),
        ("normalized_now", "Имён от модели сейчас", ""),
        ("normalized_cached", "Имён из кэша", ""),
        ("normalize_failures", "Не удалось нормализовать", "failed"),
        ("charged_entities", "Со статьями УК", ""),
        ("normalize_unasked", "Не спрошено: лимит расходов", "failed"),
        ("model_cost_usd", "Стоимость модели, $", ""),
        ("charges", "Связей со статьями УК", ""),
    )
    return card(
        run,
        where='<a href="/ui/entities">Сущности</a>',
        progress=progress,
        body=_summary(_totals(run), labels),
    )


_ROSFIN_STAGE = re.compile(r"event=entities_rf_check_stage stage=([^\n]+)")
_ROSFIN_STAGES = {
    "downloading": "Скачиваю перечень с fedsfm.ru…",
    "importing": "Перечень изменился — сохраняю новый снимок…",
    "matching": "Сверяю сущности с перечнем…",
    "writing": "Сохраняю…",
    "merging": "Сливаю спорные пары, где человек в перечне…",
}


def _rosfin_card(run: OperationRun) -> str:
    """A check against the list: its stage while it runs; the snapshot and counts after."""
    stage = _last_stage(_ROSFIN_STAGE, run.stderr)
    totals = _totals(run)
    lines: list[str] = []
    if totals.get("snapshot_id"):
        snapshot_date = str(totals.get("snapshot_date") or "")[:10]
        lines.append(
            f"<p>Перечень: снимок #{totals['snapshot_id']} от {escape(snapshot_date)}, "
            f"записей {totals.get('entries', 0)}"
            f"{' — <b>новый</b>' if totals.get('new_snapshot') else ' — не изменился'}.</p>"
        )
        lines.append(
            "<p>"
            + badge(f"В перечне (ФИО с отчеством): {totals.get('rf_full', 0)}", "failed")
            + " "
            + badge(f"Возможно в перечне: {totals.get('rf_possible', 0)}", "pending")
            + " "
            + badge(f"Сущностей сверено: {totals.get('entities', 0)}")
            + " "
            + badge(f"Спорных пар слито по перечню: {totals.get('rf_merged', 0)}")
            + " "
            + badge(f"Слито «одно ФИО — один человек»: {totals.get('region_merged', 0)}")
            + ' <a href="/ui/entities">Сущности</a></p>'
        )
    if totals.get("download_error"):
        lines.append(
            '<p class="warning">Свежий перечень скачать не удалось, сверено по последнему '
            f"снимку: {escape(str(totals['download_error']))}</p>"
        )
    return card(
        run,
        progress=progress_box(_ROSFIN_STAGES.get(stage, _PREPARING)),
        body="".join(lines),
    )


_FIGURANTS_STAGE = re.compile(r"event=entity_figurants_stage stage=([^\n]+)")
_FIGURANTS_STAGES = {"reading": "Читаю сущности и цитаты…", "writing": "Сохраняю…"}


def _figurants_card(run: OperationRun) -> str:
    """Finding the figurants: the model's progress while it runs; the roles after."""
    stage = _last_stage(_FIGURANTS_STAGE, run.stderr)
    counted = _counted(stage, "asking")
    progress = (
        progress_box("Модель читает цитаты", *counted)
        if counted
        else progress_box(_FIGURANTS_STAGES.get(stage, _PREPARING))
    )
    labels = (
        ("figurant_rules", "Фигуранты по статье УК без ответа модели", "succeeded"),
        ("figurant_model", "Фигуранты по ответу модели", "succeeded"),
        ("figurant_manual", "Фигуранты по решению оператора", "succeeded"),
        ("officials", "Должностные лица", ""),
        ("officials_listed", "Добавлено в список должностных лиц", "succeeded"),
        ("possible", "Задержаны, обысканы или административное дело", "pending"),
        ("mentioned", "Только упомянуты", ""),
        ("unclear", "Не ясно", ""),
        ("failures", "Модель не ответила", "failed"),
        ("asked_now", "Ответов модели сейчас", ""),
        ("cached", "Из кэша", ""),
        ("unasked", "Не спрошено: лимит расходов", "failed"),
        ("cost_usd", "Стоимость модели, $", ""),
    )
    return card(
        run,
        where='<a href="/ui/entities">Сущности</a>',
        progress=progress,
        body=_summary(_totals(run), labels),
    )


@dataclass(frozen=True)
class _Phase:
    """A phase of the final step. It logs `event=<event> stage=<reading|asking N/M|writing>`."""

    event: str
    label: str
    # What the model is doing while the phase asks it; empty for a phase that asks none.
    asking: str = ""


# In order: the list, the verdicts, the roundup posts, the latest news, the unnamed.
_FINAL_PHASES = (
    _Phase("entities_rf_check_stage", "Сверка с перечнем"),
    _Phase("entity_politics_stage", "Политичность", "Модель читает дела"),
    _Phase("article_digest_stage", "Сводки новостей", "Модель отличает сводки от новостей"),
    _Phase("entity_news_stage", "Свежая новость", "Модель определяет, что нового по делу"),
    _Phase("unnamed_stage", "Безымянные", "Модель читает предложения о безымянных"),
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


def final_progress(stderr: str) -> str:
    """Where the final step is, from its log: which of the five phases, and within it the
    bar of the model's answers or the word of what it is doing."""
    stages = _FINAL_STAGE.findall(stderr)
    if not stages:
        return progress_box(_PREPARING)
    event, stage = stages[-1][0], stages[-1][1].strip()
    number, phase = next(
        (number, phase)
        for number, phase in enumerate(_FINAL_PHASES, start=1)
        if phase.event == event
    )
    where = f"Этап {number} из {len(_FINAL_PHASES)} · "
    counted = _counted(stage, "asking")
    if counted:
        return progress_box(f"{where}{phase.asking}", *counted)
    if phase is _FINAL_PHASES[0]:
        return progress_box(where + _FINAL_RF_STAGES.get(stage, _PREPARING))
    words = {"reading": f"{phase.label}: читаю данные…", "writing": f"{phase.label}: сохраняю…"}
    return progress_box(where + words.get(stage, _PREPARING))


def _political_card(run: OperationRun) -> str:
    """Telling persecution from crime: the model's progress while it runs; the verdicts after."""
    totals = _totals(run)
    snapshot = ""
    if totals.get("snapshot_id"):
        snapshot_date = str(totals.get("snapshot_date") or "")[:10]
        snapshot = (
            f'<p class="muted">Сверено по снимку перечня РФМ #{escape(str(totals["snapshot_id"]))}'
            f"{f' от {escape(snapshot_date)}' if snapshot_date else ''}.</p>"
        )
    elif totals:
        # The step ran without the list: none imported yet, or the check failed.
        snapshot = (
            '<p class="warning">Сверка с РФМ не выполнена: '
            f"{escape(str(totals['rf_error'])) if totals.get('rf_error') else 'снимков перечня нет'}"
            ". Политичность оценена без неё.</p>"
        )
    if totals.get("download_error"):
        snapshot += (
            '<p class="warning">Свежий перечень не скачан, сверено по последнему сохранённому '
            f"снимку: {escape(str(totals['download_error']))}</p>"
        )
    labels = (
        # The list confirms who a person is: no mark against them.
        ("rf_full", "В перечне (ФИО с отчеством)", ""),
        ("rf_possible", "Возможно в перечне", "pending"),
        ("rf_merged", "Спорных пар слито по перечню", ""),
        ("region_merged", "Слито «одно ФИО — один человек»", ""),
        ("political_rules", "Политические по статье УК", "succeeded"),
        ("political_model", "Политические по ответу модели", "succeeded"),
        ("political_memorial", "Политические по категории «Мемориала»", "succeeded"),
        ("political_manual", "Политические по решению оператора", "succeeded"),
        ("criminal_rules", "Уголовные по статье (без модели)", ""),
        ("criminal_manual", "Уголовные по решению оператора", ""),
        ("criminal", "Уголовные", ""),
        ("unclear", "Не ясно", ""),
        ("failures", "Модель не ответила", "failed"),
        ("asked_now", "Ответов модели сейчас", ""),
        ("cached", "Из кэша", ""),
        ("unasked", "Не спрошено: лимит расходов", "failed"),
        ("cost_usd", "Стоимость модели, $", ""),
        ("news_new_case", "Свежая новость: новое дело", "succeeded"),
        ("news_sentence", "Свежая новость: приговор", "succeeded"),
        ("news_ongoing", "Свежая новость: продолжение дела", ""),
        ("news_closed", "Свежая новость: дело завершено", ""),
        ("news_unknown", "Свежая новость не определена", "pending"),
        ("unnamed", "Безымянных фигурантов", "succeeded"),
        ("unnamed_cost_usd", "Стоимость (безымянные), $", ""),
    )
    return card(
        run,
        where='<a href="/ui/political">Результат</a>',
        progress=final_progress(run.stderr),
        body=snapshot + _summary(totals, labels),
    )


# The runs over the whole database: no source selection, a card of their own.
WHOLE_DATABASE_CARDS: dict[str, Callable[[OperationRun], str]] = {
    "purge": _purge_card,
    "entities": _entities_card,
    "rosfin": _rosfin_card,
    "figurants": _figurants_card,
    "political": _political_card,
}

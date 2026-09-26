"""Manual pipeline runs: step 1 loads every news source, steps 2–5 the whole database."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from html import escape
from typing import Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from db.orm_models import MonitoringRunRecord
from monitoring.models import MonitoringRunStatus, MonitoringRunView, MonitoringTrigger
from monitoring.repository import SqlAlchemyMonitoringRepository
from operator_console import (
    OperationConflictError,
    OperationNotFoundError,
    OperationParameters,
    OperationRegistry,
    OperationRun,
    OperationRunStatus,
)
from sources.source_registry import news_sources
from web.dependencies import get_db, get_operation_registry, session_factory_for
from web.ui.funnel import funnel, funnel_html
from web.ui.layout import _page
from web.ui.pipeline import PipelineState, current_state, out_of_turn, stepper

router = APIRouter()

_OPERATION = "monitor"
# Runs over the whole database: no source selection, a card of their own. `rosfin` stays
# here so old standalone runs remain accessible in the UI.
_WHOLE_DATABASE = ("purge", "entities", "rosfin", "figurants", "political")
_RUN_STATUS_LABELS = {
    OperationRunStatus.PENDING: "В очереди",
    OperationRunStatus.RUNNING: "Выполняется",
    OperationRunStatus.SUCCEEDED: "Завершено",
    OperationRunStatus.FAILED: "Завершено с ошибками",
    OperationRunStatus.INTERRUPTED: "Прервано",
}
# Badge colours of `local-ui.css`: yellow while in progress or partly done, green, red.
_RUN_STATUS_BADGES = {
    OperationRunStatus.PENDING: "pending",
    OperationRunStatus.RUNNING: "running",
    OperationRunStatus.SUCCEEDED: "succeeded",
    OperationRunStatus.FAILED: "failed",
    OperationRunStatus.INTERRUPTED: "failed",
}
HISTORY_SIZE = 4


def _stop_form(run: OperationRun, back: str) -> str:
    """A button that stops a live run; `back` is the page to return to."""
    return (
        f'<form method="post" action="/ui/management/runs/{run.id}/stop" class="stop-form" '
        "onsubmit=\"return confirm('Остановить загрузку? Уже загруженное останется в базе.')\">"
        f'<input type="hidden" name="back" value="{escape(back, quote=True)}">'
        '<button type="submit" class="danger">Остановить</button></form>'
    )


def _badge(label: str, css_class: str = "") -> str:
    return f'<span class="badge {css_class}">{escape(label)}</span>'


def _local_time(moment: datetime) -> str:
    return moment.astimezone().strftime("%d.%m.%Y %H:%M")


_MODE_TITLES = {
    "load": "Загрузка статей",
    "resolve": "Разрешение персон",
    "purge": "Очистка от мусора",
    "entities": "Сборка сущностей",
    "rosfin": "Сверка с Росфинмониторингом",
    "figurants": "Поиск фигурантов",
    "political": "Политические дела и сверка с РФМ",
    None: "Загрузка и разрешение",
}


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


def _column_legend(columns: Sequence[_Column]) -> str:
    items = "".join(
        f"<li><b>{escape(header)}</b> — {escape(meaning)}</li>" for header, meaning, _ in columns
    )
    return f'<ul class="column-legend muted">{items}</ul>'


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


def _history(runs: Sequence[OperationRun], current: OperationRun | None) -> str:
    if not runs:
        return ""
    rows = "".join(
        f'<tr class="{"current" if current is not None and run.id == current.id else ""}">'
        f'<td><a href="/ui/management?run_id={run.id}">#{run.id}</a></td>'
        f"<td>{_MODE_TITLES[run.parameters.mode]}</td>"
        f"<td>{escape(_local_time(run.created_at))}</td>"
        f"<td>{_badge(_RUN_STATUS_LABELS[run.status], _RUN_STATUS_BADGES[run.status])}</td>"
        f'<td class="num">{len(run.parameters.sources or [])}</td>'
        "</tr>"
        for run in runs
    )
    return f"""<section class="band">
  <h2>Последние ручные запуски</h2>
  <table><thead><tr><th>Запуск</th><th>Что</th><th>Начат</th><th>Статус</th><th>Источников</th></tr></thead>
  <tbody>{rows}</tbody></table>
</section>"""


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
    began = run.started_at or run.created_at
    return {
        source
        for source in db.scalars(
            select(MonitoringRunRecord.source).where(
                MonitoringRunRecord.source.in_(list(sources)),
                MonitoringRunRecord.started_at < began,
                or_(
                    MonitoringRunRecord.finished_at.is_(None),
                    MonitoringRunRecord.finished_at > began,
                ),
            )
        ).all()
        if source is not None
    }


def _monitoring_runs(db: Session, run: OperationRun) -> list[MonitoringRunView]:
    """The monitoring runs of one manual run: one per source, then the shared derived one."""
    return SqlAlchemyMonitoringRepository(session_factory_for(db)).list_runs_started_between(
        run.started_at or run.created_at,
        run.finished_at,
        trigger=MonitoringTrigger.MANUAL,
    )


def _duration(seconds: float) -> str:
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes:02d} мин" if hours else f"{minutes} мин {seconds:02d} с"


def _published_range_label(run: OperationRun) -> str:
    published_from = run.parameters.published_from
    published_to = run.parameters.published_to
    if published_from is None and published_to is None:
        return ""
    if published_from is not None and published_to is not None:
        label = f"{published_from} — {published_to}"
    elif published_from is not None:
        label = f"с {published_from}"
    else:
        label = f"по {published_to}"
    return f'<p class="muted">Период публикаций: <strong>{escape(label)}</strong></p>'


def _progress(db: Session, run: OperationRun) -> str:
    """Where a live manual run is: which source of how many, and how far into it.

    Built from the counters monitoring writes as it goes; empty once the run ended."""
    if run.status not in (OperationRunStatus.PENDING, OperationRunStatus.RUNNING):
        return ""
    selected = run.parameters.sources or []
    monitoring_runs = _monitoring_runs(db, run)
    per_source = {item.source: item for item in monitoring_runs if item.source in selected}
    derived = next((item for item in monitoring_runs if item.source is None), None)
    running = MonitoringRunStatus.RUNNING
    steps = len(selected) + _has_derived_step(run)
    done = sum(item.status is not running for item in per_source.values())
    names = {item.name: item.source_name for item in news_sources()}
    current = next((item for item in per_source.values() if item.status is running), None)
    detail = ""
    if run.status is OperationRunStatus.PENDING or run.started_at is None:
        step = "Запуск готовится…"
    elif derived is not None:
        done = len(selected) + (derived.status is not running)
        step = f"Шаг {steps} из {steps}: общая классификация и сверка с РФМ"
    elif current is not None and current.source is not None:
        step = (
            f"Источник {done + 1} из {len(selected)}: "
            f"{escape(names.get(current.source, current.source))}"
        )
        handled = current.documents_skipped + current.documents_ingested + current.documents_failed
        resolution = _resolution_progress(current)
        if resolution is not None:
            resolved, total = resolution
            detail = (
                f'<progress class="step" value="{resolved}" max="{max(total, 1)}"></progress>'
                f'<p class="muted">Разрешение персон: статей {resolved} из {total}; '
                f"новых персон {current.persons_created}, привязано {current.persons_linked}, "
                f"на ревью {current.person_reviews_created}</p>"
            )
        elif run.parameters.mode == "resolve":
            detail = '<p class="muted">Ищу статьи с неразобранными упоминаниями…</p>'
        elif current.documents_discovered == 0:
            detail = '<p class="muted">Ищу новые публикации…</p>'
        elif handled < current.documents_discovered:
            detail = (
                f'<progress class="step" value="{handled}" max="{current.documents_discovered}">'
                "</progress>"
                f'<p class="muted">Документов {handled} из {current.documents_discovered}: '
                f"загружено {current.documents_ingested}, уже были {current.documents_skipped}, "
                f"ошибок {current.documents_failed}</p>"
            )
        else:
            detail = (
                f'<p class="muted">Документы загружены ({current.documents_ingested} новых), '
                "извлекаю людей и события…</p>"
            )
    else:
        step = f"Перехожу к следующему источнику ({done} из {len(selected)} готово)…"
    started = run.started_at or run.created_at
    elapsed = _duration((datetime.now(UTC) - started).total_seconds())
    return f"""<div class="progress-box">
  <progress class="overall" value="{done}" max="{steps}"></progress>
  <p><strong>{step}</strong> <span class="muted">· готово {done} из {steps} шагов · идёт {elapsed}</span></p>
  {detail}
</div>"""


def _source_title(source: str, names: dict[str, str]) -> str:
    return (
        f"{escape(names[source])}<br><code>{escape(source)}</code>"
        if source in names
        else f"<code>{escape(source)}</code>"
    )


_PURGE_PROGRESS = re.compile(
    r"event=junk_purge_progress articles=(\d+) total=(\d+) persons=(\d+) reviews=(\d+)"
    r"(?: outdated=(\d+))?"
)


def _purge_card(run: OperationRun) -> str:
    """A purge has no sources: its card counts what it removed, from its own log."""
    in_progress = run.status in (OperationRunStatus.PENDING, OperationRunStatus.RUNNING)
    found = _PURGE_PROGRESS.findall(run.stderr)
    # The last progress line holds the running totals; none yet before the first batch.
    articles, total, persons, reviews, outdated = (
        int(value or 0) for value in (found[-1] if found else ("0",) * 5)
    )
    progress = (
        f'<div class="progress-box"><progress class="overall" value="{articles}" '
        f'max="{max(total, 1)}"></progress>'
        f"<p><strong>Удалено статей {articles} из {total}</strong></p></div>"
        if in_progress and found
        else '<div class="progress-box"><p><strong>Ищу статьи без уголовных дел…</strong></p></div>'
        if in_progress
        else ""
    )
    summary = " ".join(
        (
            _badge(f"Статей удалено: {articles}", "succeeded"),
            _badge(f"Из них до рабочей даты: {outdated}", ""),
            _badge(f"Людей удалено: {persons}", "succeeded"),
            _badge(f"Записей проверки удалено: {reviews}", ""),
        )
    )
    overall = _badge(_RUN_STATUS_LABELS[run.status], _RUN_STATUS_BADGES[run.status])
    started = escape(run.created_at.astimezone().strftime("%d.%m.%Y %H:%M"))
    refresh = (
        "<script>setTimeout(() => window.location.reload(), 5000);</script>" if in_progress else ""
    )
    return f"""<section class="band run-card">
  <h2>Запуск #{run.id} · {_MODE_TITLES["purge"]} {overall}</h2>
  <p class="muted">Начат {started} · вся база · <a href="/ui/logs?run_id={run.id}">Лог запуска</a></p>
  {progress}
  <p class="run-summary">{summary}</p>
  <p class="muted">Удаляются статьи, в последнем разборе которых нет уголовного события,
  со всем извлечённым из них, и люди, которых после этого ничто не упоминает.
  Исходная публикация остаётся пустой отметкой, чтобы её не скачивать снова.</p>
  {refresh}
</section>"""


_ENTITIES_STAGE = re.compile(r"event=entities_collect_stage stage=([^\n]+)")
_ENTITIES_STAGES = {
    "reading": "Читаю упоминания…",
    "grouping": "Склеиваю…",
    "writing": "Сохраняю…",
    "charges": "Связываю со статьями УК…",
}


def _entities_card(run: OperationRun) -> str:
    """An entity rebuild: its stage from the log while it runs, its counts at the end."""
    in_progress = run.status in (OperationRunStatus.PENDING, OperationRunStatus.RUNNING)
    stages = _ENTITIES_STAGE.findall(run.stderr)
    stage = stages[-1].strip() if stages else ""
    if stage.startswith("normalizing "):
        done, _, total = stage.removeprefix("normalizing ").partition("/")
        progress = (
            f'<div class="progress-box"><progress class="overall" value="{escape(done)}" '
            f'max="{escape(total)}"></progress><p><strong>Модель приводит имена к '
            f"именительному: {escape(done)} из {escape(total)}</strong></p></div>"
        )
    else:
        progress = (
            f'<div class="progress-box"><p><strong>'
            f"{escape(_ENTITIES_STAGES.get(stage, 'Готовлюсь…'))}</strong></p></div>"
        )
    try:
        totals = json.loads(run.stdout) if run.stdout else {}
    except json.JSONDecodeError:
        totals = {}
    if not isinstance(totals, dict):
        totals = {}
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
    summary = " ".join(
        _badge(f"{label}: {totals[key]}", badge)
        for key, label, badge in labels
        if isinstance(totals, dict) and totals.get(key)
    )
    overall = _badge(_RUN_STATUS_LABELS[run.status], _RUN_STATUS_BADGES[run.status])
    started = escape(run.created_at.astimezone().strftime("%d.%m.%Y %H:%M"))
    refresh = (
        "<script>setTimeout(() => window.location.reload(), 5000);</script>" if in_progress else ""
    )
    return f"""<section class="band run-card">
  <h2>Запуск #{run.id} · {_MODE_TITLES["entities"]} {overall}</h2>
  <p class="muted">Начат {started} · <a href="/ui/entities">Сущности</a> ·
  <a href="/ui/logs?run_id={run.id}">Лог запуска</a></p>
  {progress if in_progress else ""}
  <p class="run-summary">{summary}</p>
  {refresh}
</section>"""


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
    in_progress = run.status in (OperationRunStatus.PENDING, OperationRunStatus.RUNNING)
    stages = _ROSFIN_STAGE.findall(run.stderr)
    stage = stages[-1].strip() if stages else ""
    try:
        totals = json.loads(run.stdout) if run.stdout else {}
    except json.JSONDecodeError:
        totals = {}
    if not isinstance(totals, dict):
        totals = {}
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
            + _badge(f"В перечне (ФИО с отчеством): {totals.get('rf_full', 0)}", "failed")
            + " "
            + _badge(f"Возможно в перечне: {totals.get('rf_possible', 0)}", "pending")
            + " "
            + _badge(f"Сущностей сверено: {totals.get('entities', 0)}")
            + " "
            + _badge(f"Спорных пар слито по перечню: {totals.get('rf_merged', 0)}")
            + " "
            + _badge(f"Слито «одно ФИО — один человек»: {totals.get('region_merged', 0)}")
            + ' <a href="/ui/entities">Сущности</a></p>'
        )
    if totals.get("download_error"):
        lines.append(
            '<p class="warning">Свежий перечень скачать не удалось, сверено по последнему '
            f"снимку: {escape(str(totals['download_error']))}</p>"
        )
    progress = (
        f'<div class="progress-box"><p><strong>'
        f"{escape(_ROSFIN_STAGES.get(stage, 'Готовлюсь…'))}</strong></p></div>"
        if in_progress
        else ""
    )
    overall = _badge(_RUN_STATUS_LABELS[run.status], _RUN_STATUS_BADGES[run.status])
    started = escape(run.created_at.astimezone().strftime("%d.%m.%Y %H:%M"))
    refresh = (
        "<script>setTimeout(() => window.location.reload(), 5000);</script>" if in_progress else ""
    )
    return f"""<section class="band run-card">
  <h2>Запуск #{run.id} · {_MODE_TITLES["rosfin"]} {overall}</h2>
  <p class="muted">Начат {started} · <a href="/ui/logs?run_id={run.id}">Лог запуска</a></p>
  {progress}
  {"".join(lines)}
  {refresh}
</section>"""


_FIGURANTS_STAGE = re.compile(r"event=entity_figurants_stage stage=([^\n]+)")


def _figurants_card(run: OperationRun) -> str:
    """Finding the figurants: the model's progress while it runs; the roles after."""
    in_progress = run.status in (OperationRunStatus.PENDING, OperationRunStatus.RUNNING)
    stages = _FIGURANTS_STAGE.findall(run.stderr)
    stage = stages[-1].strip() if stages else ""
    if stage.startswith("asking "):
        done, _, total = stage.removeprefix("asking ").partition("/")
        progress = (
            f'<div class="progress-box"><progress class="overall" value="{escape(done)}" '
            f'max="{escape(total)}"></progress><p><strong>Модель читает цитаты: '
            f"{escape(done)} из {escape(total)}</strong></p></div>"
        )
    else:
        label = {"reading": "Читаю сущности и цитаты…", "writing": "Сохраняю…"}.get(
            stage, "Готовлюсь…"
        )
        progress = f'<div class="progress-box"><p><strong>{label}</strong></p></div>'
    try:
        totals = json.loads(run.stdout) if run.stdout else {}
    except json.JSONDecodeError:
        totals = {}
    if not isinstance(totals, dict):
        totals = {}
    labels = (
        ("figurant_rules", "Фигуранты по статье УК без ответа модели", "succeeded"),
        ("figurant_model", "Фигуранты по ответу модели", "succeeded"),
        ("officials", "Должностные лица", ""),
        ("possible", "Задержаны, обысканы или административное дело", "pending"),
        ("mentioned", "Только упомянуты", ""),
        ("unclear", "Не ясно", ""),
        ("failures", "Модель не ответила", "failed"),
        ("asked_now", "Ответов модели сейчас", ""),
        ("cached", "Из кэша", ""),
        ("unasked", "Не спрошено: лимит расходов", "failed"),
        ("cost_usd", "Стоимость модели, $", ""),
    )
    summary = " ".join(
        _badge(f"{label}: {totals[key]}", badge)
        for key, label, badge in labels
        if isinstance(totals, dict) and totals.get(key)
    )
    overall = _badge(_RUN_STATUS_LABELS[run.status], _RUN_STATUS_BADGES[run.status])
    started = escape(run.created_at.astimezone().strftime("%d.%m.%Y %H:%M"))
    refresh = (
        "<script>setTimeout(() => window.location.reload(), 5000);</script>" if in_progress else ""
    )
    return f"""<section class="band run-card">
  <h2>Запуск #{run.id} · {_MODE_TITLES["figurants"]} {overall}</h2>
  <p class="muted">Начат {started} · <a href="/ui/entities">Сущности</a> ·
  <a href="/ui/logs?run_id={run.id}">Лог запуска</a></p>
  {progress if in_progress else ""}
  <p class="run-summary">{summary}</p>
  {refresh}
</section>"""


_FINAL_STAGE = re.compile(r"event=(entities_rf_check_stage|entity_politics_stage) stage=([^\n]+)")
_FINAL_RF_STAGES = {
    "downloading": "Скачиваю перечень с fedsfm.ru…",
    "importing": "Перечень изменился — сохраняю новый снимок…",
    "matching": "Сверяю сущности с перечнем…",
    "writing": "Сохраняю сверку с РФМ…",
    "merging": "Сливаю спорные пары по перечню…",
}


def _political_card(run: OperationRun) -> str:
    """Telling persecution from crime: the model's progress while it runs; the verdicts after."""
    in_progress = run.status in (OperationRunStatus.PENDING, OperationRunStatus.RUNNING)
    stages = _FINAL_STAGE.findall(run.stderr)
    stage_source, stage = stages[-1] if stages else ("", "")
    stage = stage.strip()
    if stage_source == "entity_politics_stage" and stage.startswith("asking "):
        done, _, total = stage.removeprefix("asking ").partition("/")
        progress = (
            f'<div class="progress-box"><progress class="overall" value="{escape(done)}" '
            f'max="{escape(total)}"></progress><p><strong>Модель читает дела: '
            f"{escape(done)} из {escape(total)}</strong></p></div>"
        )
    else:
        label = (
            _FINAL_RF_STAGES.get(stage, "Готовлюсь…")
            if stage_source == "entities_rf_check_stage"
            else {"reading": "Читаю фигурантов и цитаты…", "writing": "Сохраняю…"}.get(
                stage, "Готовлюсь…"
            )
        )
        progress = f'<div class="progress-box"><p><strong>{label}</strong></p></div>'
    try:
        totals = json.loads(run.stdout) if run.stdout else {}
    except json.JSONDecodeError:
        totals = {}
    if not isinstance(totals, dict):
        totals = {}
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
        ("criminal_rules", "Уголовные по статье (без модели)", ""),
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
    summary = " ".join(
        _badge(f"{label}: {totals[key]}", badge)
        for key, label, badge in labels
        if isinstance(totals, dict) and totals.get(key)
    )
    overall = _badge(_RUN_STATUS_LABELS[run.status], _RUN_STATUS_BADGES[run.status])
    started = escape(run.created_at.astimezone().strftime("%d.%m.%Y %H:%M"))
    refresh = (
        "<script>setTimeout(() => window.location.reload(), 5000);</script>" if in_progress else ""
    )
    return f"""<section class="band run-card">
  <h2>Запуск #{run.id} · {_MODE_TITLES["political"]} {overall}</h2>
  <p class="muted">Начат {started} · <a href="/ui/political">Результат</a> ·
  <a href="/ui/logs?run_id={run.id}">Лог запуска</a></p>
  {progress if in_progress else ""}
  {snapshot}
  <p class="run-summary">{summary}</p>
  {refresh}
</section>"""


def _run_results(db: Session, run: OperationRun, selected: Sequence[str]) -> str:
    if run.parameters.mode == "purge":
        return _purge_card(run)
    if run.parameters.mode == "entities":
        return _entities_card(run)
    if run.parameters.mode == "rosfin":
        return _rosfin_card(run)
    if run.parameters.mode == "figurants":
        return _figurants_card(run)
    if run.parameters.mode == "political":
        return _political_card(run)
    """A card per run: status, counts per outcome and, folded, the sources that ran.

    Sources the run never reached are only counted: listed one by one they buried the
    source selection under dozens of «Не запускался» rows."""
    monitoring_runs = _monitoring_runs(db, run)
    per_source = {item.source: item for item in monitoring_runs if item.source is not None}
    derived = next((item for item in monitoring_runs if item.source is None), None)
    skipped = _skipped_sources(run)
    unreached = [source for source in selected if source not in per_source]
    busy = _busy_sources(db, run, unreached) | {
        source for source, reason in skipped.items() if reason == "already_running"
    }

    names = {item.name: item.source_name for item in news_sources()}
    in_progress = run.status in (OperationRunStatus.PENDING, OperationRunStatus.RUNNING)
    resolving = run.parameters.mode == "resolve"
    columns = (*(_RESOLVE_COLUMNS if resolving else _LOAD_COLUMNS), _ERRORS_COLUMN)
    # The number columns and «Сообщение», for a row without numbers.
    blank = len(columns) + 1
    counts = dict.fromkeys(_OUTCOMES, 0)
    rows: list[str] = []
    for source in selected:
        item = per_source.get(source)
        if item is None:
            if source in busy:
                counts["busy"] += 1
                rows.append(
                    f"<tr><td>{_source_title(source, names)}</td>"
                    f"<td>{_badge('Занят', 'partial')}</td>"
                    f'<td colspan="{blank}"></td></tr>'
                )
            else:
                counts["waiting" if in_progress else "not_started"] += 1
            continue
        outcome = _source_outcome(item.status, in_progress=in_progress)
        counts[outcome] += 1
        numbers = "".join(f'<td class="num">{value(item)}</td>' for _, _, value in columns)
        message = escape(item.error_message[:240]) if item.error_message else ""
        label, badge = _OUTCOMES[outcome]
        rows.append(
            f"<tr><td>{_source_title(source, names)}</td><td>{_badge(label, badge)}</td>"
            f'{numbers}<td class="error-text">{message}</td></tr>'
        )

    if _has_derived_step(run):
        if derived is not None:
            label, badge = _OUTCOMES[_source_outcome(derived.status, in_progress=in_progress)]
            derived_status = _badge(label, badge)
            derived_metrics = (
                f"Классифицировано: {derived.classifications_created}; совпадений РФМ: "
                f"{derived.rf_matches_created}; ошибок: {derived.error_count}"
            )
        else:
            derived_status = _badge("Ожидает", "pending") if in_progress else _badge("Не запущен")
            derived_metrics = ""
        rows.append(
            f'<tr class="derived"><td>Общая классификация и сверка с РФМ</td>'
            f'<td>{derived_status}</td><td colspan="{blank}">{derived_metrics}</td></tr>'
        )
    headers = "".join(
        f'<th title="{escape(meaning, quote=True)}">{escape(header)}</th>'
        for header, meaning, _ in columns
    )
    overall = _RUN_STATUS_LABELS[run.status]
    overall_badge = _RUN_STATUS_BADGES[run.status]
    if run.status is OperationRunStatus.FAILED and any(
        item.status in (MonitoringRunStatus.COMPLETED, MonitoringRunStatus.COMPLETED_WITH_ERRORS)
        for item in monitoring_runs
    ):
        overall, overall_badge = "Завершено частично: есть ошибки", "partial"
    summary = " ".join(
        _badge(f"{_OUTCOMES[outcome][0]}: {count}", _OUTCOMES[outcome][1])
        for outcome, count in counts.items()
        if count
    )
    refresh = (
        "<script>setTimeout(() => window.location.reload(), 5000);</script>" if in_progress else ""
    )
    started = escape(run.created_at.astimezone().strftime("%d.%m.%Y %H:%M"))
    ran = len(selected) - counts["waiting"] - counts["not_started"]
    return f"""<section class="band run-card">
  <h2>Запуск #{run.id} · {_MODE_TITLES[run.parameters.mode]} {_badge(overall, overall_badge)}</h2>
  <p class="muted">Начат {started} · источников выбрано: {len(selected)} ·
  <a href="/ui/logs?run_id={run.id}">Лог запуска</a></p>
  {_published_range_label(run)}
  {_progress(db, run)}
  <p class="run-summary">{summary}</p>
  <details{" open" if in_progress else ""}>
    <summary>Подробно по источникам ({ran})</summary>
    <table><thead><tr><th>Источник / этап</th><th>Статус</th>{headers}
    <th title="Текст ошибки, если источник упал целиком">Сообщение</th></tr></thead>
    <tbody>{"".join(rows)}</tbody></table>
    {_column_legend(columns)}
  </details>
  {refresh}
</section>"""


SOURCE_ERRORS = 8
# A source's failed loads, the latest first: parsing and network errors of step 1.
_SOURCE_ERRORS = text(
    """
    SELECT r.source, i.stage, i.error_type, left(i.error_message, 240) AS message, i.created_at
    FROM monitoring_run_items i JOIN monitoring_runs r ON r.id = i.run_id
    WHERE i.status = 'failed'
    ORDER BY i.created_at DESC, i.id DESC
    LIMIT :limit
    """
)
_RECENT_SOURCE_ERRORS = text(
    """
    SELECT count(DISTINCT r.source)
    FROM monitoring_run_items i JOIN monitoring_runs r ON r.id = i.run_id
    WHERE i.status = 'failed' AND i.created_at >= now() - interval '7 days'
    """
)


def recent_source_errors(db: Session) -> int:
    """The sources that failed to load in the last week."""
    return db.scalar(_RECENT_SOURCE_ERRORS) or 0


def _source_errors(db: Session) -> str:
    rows = "".join(
        f"<tr><td>{escape(source or 'общий проход')}</td><td>{escape(stage or '—')}</td>"
        f"<td>{escape(error_type or '')}: {escape(message or '')}</td>"
        f"<td>{created_at.astimezone():%d.%m.%Y}</td></tr>"
        for source, stage, error_type, message, created_at in db.execute(
            _SOURCE_ERRORS, {"limit": SOURCE_ERRORS}
        ).all()
    )
    table = (
        f"""<table><caption class="visually-hidden">Последние ошибки</caption>
  <thead><tr><th scope="col">Источник</th><th scope="col">Этап</th><th scope="col">Ошибка</th>
  <th scope="col">Когда</th></tr></thead><tbody>{rows}</tbody></table>"""
        if rows
        else '<p class="empty">Ошибок нет.</p>'
    )
    return f"""<section class="band" id="source-errors" aria-labelledby="errors-title">
  <h2 id="errors-title">Ошибки источников</h2>
  {table}
</section>"""


def _management_page(
    db: Session,
    *,
    run: OperationRun | None = None,
    history: Sequence[OperationRun] = (),
    state: PipelineState | None = None,
    warning: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    definitions = news_sources()
    run_html = _run_results(db, run, run.parameters.sources or []) if run else ""
    error_html = f'<p class="warning">{escape(warning)}</p>' if warning else ""
    body = f"""{error_html}
{run_html}
{funnel_html(funnel(db))}
<form method="post" action="/ui/management/run" class="source-form">
  <div class="run-bar">
    {stepper(state or PipelineState(current="load"), len(definitions))}
    <span class="muted">Шаг 1 скачивает все новостные источники ({len(definitions)}); шаги 2–5
    работают со всей базой.</span>
  </div>
  <fieldset class="date-range">
    <legend>Даты публикаций для шага 1</legend>
    <label>С <input type="date" name="published_from"></label>
    <label>По <input type="date" name="published_to"></label>
    <p class="muted">Пусто — без ограничения. Фильтр применяется к дате публикации после
    загрузки статьи; для старых дат увеличьте limit источника.</p>
  </fieldset>
</form>
{_history(history, run)}
{_source_errors(db)}"""
    page = _page(
        "Управление",
        body,
        active="management",
        instruction=(
            "Пять шагов по кругу: подгрузить статьи → очистить от мусора → собрать "
            "сущности → определить фигурантов → выделить политические дела и сверить "
            "с РФМ. Нажать можно только подсвеченный шаг."
        ),
        next_action="Шаг 1 скачивает все новостные источники; шаги 2–5 — вся база.",
        db=db,
    )
    page.status_code = status_code
    return page


def _recent_runs(registry: OperationRegistry) -> list[OperationRun]:
    """The latest manual runs with a source selection, newest first."""
    return [
        item
        for item in registry.runs_of(_OPERATION, limit=50)
        if item.parameters.sources is not None or item.parameters.mode in _WHOLE_DATABASE
    ][:HISTORY_SIZE]


@router.get("/ui/management", response_class=HTMLResponse)
def ui_management(
    run_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    run: OperationRun | None = None
    if run_id is not None:
        try:
            run = registry.get(run_id)
        except OperationNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Запуск не найден") from exc
        if run.operation.name != _OPERATION or (
            run.parameters.sources is None and run.parameters.mode not in _WHOLE_DATABASE
        ):
            raise HTTPException(status_code=404, detail="Запуск не найден")
    history = _recent_runs(registry)
    if run_id is None:
        run = history[0] if history else None
    return _management_page(db, run=run, history=history, state=current_state(registry))


@router.post("/ui/management/run", response_model=None)
async def start_management_run(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Step 1: load and extract every news source (or, from the API, the ones sent)."""
    return await _start(request, db, registry, "load")


@router.post("/ui/management/purge", response_model=None)
def start_management_purge(
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Step 2: delete the articles without a criminal case; the whole database."""
    return _start_whole_database(db, registry, "purge")


@router.post("/ui/management/political", response_model=None)
def start_management_political(
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Step 5: tell persecution from crime and check Rosfinmonitoring."""
    return _start_whole_database(db, registry, "political")


@router.post("/ui/management/figurants", response_model=None)
def start_management_figurants(
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Step 4: tell who a case is opened against among the entities."""
    return _start_whole_database(db, registry, "figurants")


@router.post("/ui/management/entities", response_model=None)
def start_management_entities(
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Step 3: rebuild the person entities; the whole database."""
    return _start_whole_database(db, registry, "entities")


def _refused(
    db: Session, registry: OperationRegistry, warning: str, status_code: int
) -> HTMLResponse:
    return _management_page(
        db,
        warning=warning,
        history=_recent_runs(registry),
        state=current_state(registry),
        status_code=status_code,
    )


def _start_whole_database(
    db: Session,
    registry: OperationRegistry,
    mode: Literal["purge", "entities", "rosfin", "figurants", "political"],
) -> HTMLResponse | RedirectResponse:
    refusal = out_of_turn(current_state(registry), mode)
    if refusal is not None:
        return _refused(db, registry, refusal, 409)
    try:
        run = registry.start(_OPERATION, OperationParameters(mode=mode))
    except OperationConflictError:
        # Another worker started a run between the check and the start.
        return _refused(db, registry, "Идёт другой запуск.", 409)
    return RedirectResponse(f"/ui/management?run_id={run.id}", status_code=303)


async def _start(
    request: Request, db: Session, registry: OperationRegistry, mode: Literal["load"]
) -> HTMLResponse | RedirectResponse:
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    published_from = _parse_date_field(form, "published_from")
    published_to = _parse_date_field(form, "published_to")
    if published_from == "invalid" or published_to == "invalid":
        return _refused(db, registry, "Дата должна быть в формате YYYY-MM-DD.", 400)
    if (
        isinstance(published_from, date)
        and isinstance(published_to, date)
        and published_from > published_to
    ):
        return _refused(db, registry, "Дата «С» должна быть не позже даты «По».", 400)
    everything = [item.name for item in news_sources()]
    # The page sends no sources: all of them. The API may still name some.
    selected = list(dict.fromkeys(form.get("sources", []))) or everything
    refusal = out_of_turn(current_state(registry), mode)
    if refusal is not None:
        return _refused(db, registry, refusal, 409)
    if not set(selected) <= set(everything):
        return _refused(db, registry, "В запросе есть неизвестный или не новостной источник.", 400)
    try:
        run = registry.start(
            _OPERATION,
            OperationParameters(
                sources=selected,
                mode=mode,
                published_from=published_from.isoformat()
                if isinstance(published_from, date)
                else None,
                published_to=published_to.isoformat() if isinstance(published_to, date) else None,
            ),
        )
    except OperationConflictError:
        return _refused(db, registry, "Идёт другой запуск.", 409)
    return RedirectResponse(f"/ui/management?run_id={run.id}", status_code=303)


def _parse_date_field(
    form: dict[str, list[str]], field: Literal["published_from", "published_to"]
) -> date | Literal["invalid"] | None:
    raw = (form.get(field) or [""])[0].strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return "invalid"


@router.post("/ui/management/runs/{run_id}/stop", response_model=None)
async def stop_management_run(
    run_id: int,
    request: Request,
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> RedirectResponse:
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    back = {"logs": "/ui/logs", "entities": "/ui/entities"}.get(
        (form.get("back") or [""])[0], "/ui/management"
    )
    try:
        registry.stop(run_id)
    except OperationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Запуск не найден") from exc
    # Stopping an already finished run changes nothing: the page shows how it ended.
    return RedirectResponse(f"{back}?run_id={run_id}", status_code=303)

"""Temporary news-source selection and manual pipeline runs."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from db.orm_models import MonitoringRunRecord, ParsedArticleRecord, Source, SourceDocument
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
from sources.source_registry import SourceDefinition, news_sources
from web.dependencies import get_db, get_operation_registry, session_factory_for
from web.ui.layout import _page
from web.ui.pipeline import PipelineState, current_state, out_of_turn, stepper

router = APIRouter()

_OPERATION = "monitor"
# Runs over the whole database: no source selection, a card of their own.
_WHOLE_DATABASE = ("purge", "entities")
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
# A source that has not loaded successfully for this long is worth a look.
STALE_AFTER = timedelta(days=7)
HISTORY_SIZE = 10
# Only a display filter: court press services are recognised by their name.
_COURT_NAME = re.compile(r"суд|фемид", re.IGNORECASE)
_SOURCE_KINDS = {"telegram": "Telegram", "court": "Суд", "site": "Сайт"}


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


def _source_kind(definition: SourceDefinition) -> str:
    if _COURT_NAME.search(definition.source_name):
        return "court"
    return "telegram" if definition.base_url.startswith("https://t.me/") else "site"


def _source_rows(
    db: Session,
    definitions: Sequence[SourceDefinition],
    selected: set[str],
) -> str:
    """One table row per news source: when a document of it was last fetched, and its articles.

    The fetch time comes from the documents themselves, not from the monitoring checkpoint:
    catch-up and backfill loads never write the checkpoint, so it would call a source that
    loads every day "never loaded"."""
    loads = {
        str(base_url): (int(count), fetched_at)
        for base_url, count, fetched_at in db.execute(
            select(
                Source.base_url,
                func.count(ParsedArticleRecord.id),
                func.max(SourceDocument.fetched_at),
            )
            .join(SourceDocument, SourceDocument.source_id == Source.id)
            .outerjoin(ParsedArticleRecord, ParsedArticleRecord.document_id == SourceDocument.id)
            .group_by(Source.base_url)
        ).all()
    }
    now = datetime.now(UTC)
    rows: list[str] = []
    for item in definitions:
        articles, fetched_at = loads.get(item.base_url, (0, None))
        if fetched_at is None:
            loaded = '<td class="never">никогда</td>'
        else:
            freshness = "stale" if now - fetched_at > STALE_AFTER else ""
            loaded = f'<td class="{freshness}">{escape(_local_time(fetched_at))}</td>'
        kind = _source_kind(item)
        search = f"{item.source_name} {item.name}".lower()
        rows.append(
            f'<tr data-kind="{kind}" data-search="{escape(search, quote=True)}">'
            f'<td class="pick"><input type="checkbox" name="sources" '
            f'value="{escape(item.name, quote=True)}" '
            f"{'checked' if item.name in selected else ''}></td>"
            f"<td>{escape(item.source_name)}</td>"
            f"<td><code>{escape(item.name)}</code></td>"
            f"<td>{_SOURCE_KINDS[kind]}</td>"
            f"{loaded}"
            f'<td class="num">{articles}</td>'
            "</tr>"
        )
    return "".join(rows)


def _filter_chips(definitions: Sequence[SourceDefinition]) -> str:
    counts = {kind: 0 for kind in _SOURCE_KINDS}
    for item in definitions:
        counts[_source_kind(item)] += 1
    chips = [
        f'<button type="button" class="chip active" data-kind="all">Все ({len(definitions)})</button>'
    ]
    chips += [
        f'<button type="button" class="chip" data-kind="{kind}">{label} ({counts[kind]})</button>'
        for kind, label in _SOURCE_KINDS.items()
        if counts[kind]
    ]
    return "".join(chips)


_MODE_TITLES = {
    "load": "Загрузка статей",
    "resolve": "Разрешение персон",
    "purge": "Очистка от мусора",
    "entities": "Сборка сущностей",
    None: "Загрузка и разрешение",
}


def _has_derived_step(run: OperationRun) -> bool:
    """A load leaves classification to the resolution run; the other runs end with it."""
    return run.parameters.mode not in ("load", "purge", "entities")


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
)


def _purge_card(run: OperationRun) -> str:
    """A purge has no sources: its card counts what it removed, from its own log."""
    in_progress = run.status in (OperationRunStatus.PENDING, OperationRunStatus.RUNNING)
    found = _PURGE_PROGRESS.findall(run.stderr)
    # The last progress line holds the running totals; none yet before the first batch.
    articles, total, persons, reviews = (
        int(value) for value in (found[-1] if found else ("0",) * 4)
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
    labels = (
        ("entities", "Сущностей", "succeeded"),
        ("grouped", "Упоминаний в них", ""),
        ("normalized_now", "Имён от модели сейчас", ""),
        ("normalized_cached", "Имён из кэша", ""),
        ("normalize_failures", "Не удалось нормализовать", "failed"),
        ("charged_entities", "Со статьями УК", ""),
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


def _run_results(db: Session, run: OperationRun, selected: Sequence[str]) -> str:
    if run.parameters.mode == "purge":
        return _purge_card(run)
    if run.parameters.mode == "entities":
        return _entities_card(run)
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


def _management_page(
    db: Session,
    *,
    selected: set[str],
    run: OperationRun | None = None,
    history: Sequence[OperationRun] = (),
    state: PipelineState | None = None,
    warning: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    definitions = news_sources()
    all_checked = bool(definitions) and all(item.name in selected for item in definitions)
    checked_count = sum(item.name in selected for item in definitions)
    run_html = _run_results(db, run, run.parameters.sources or []) if run else ""
    error_html = f'<p class="warning">{escape(warning)}</p>' if warning else ""
    body = f"""{error_html}
{run_html}
<form method="post" action="/ui/management/run" class="source-form">
  <div class="source-tools">
    <input id="source-search" type="search" placeholder="Поиск по названию или id" autocomplete="off">
    <div class="chips">{_filter_chips(definitions)}</div>
  </div>
  <table id="source-table" class="source-table">
    <thead><tr>
      <th class="pick"><input id="toggle-all-sources" type="checkbox" {"checked" if all_checked else ""} title="Выбрать все видимые"></th>
      <th>Источник</th><th>ID</th><th>Тип</th>
      <th title="Когда последний раз скачана публикация этого источника">Последняя загрузка</th>
      <th title="Сколько статей этого источника уже в базе">Статей в БД</th>
    </tr></thead>
    <tbody>{_source_rows(db, definitions, selected)}</tbody>
  </table>
  <div class="run-bar">
    {stepper(state or PipelineState(current="load"), checked_count)}
    <span class="muted">Выбрано <span id="selected-total">{checked_count}</span> из {len(definitions)}. Галочки — для шага 1, на расписание не влияют.
    Жёлтая дата — не загружался больше {STALE_AFTER.days} дней.</span>
  </div>
</form>
{_history(history, run)}
<script>
const rows = [...document.querySelectorAll('#source-table tbody tr')];
const boxes = rows.map(row => row.querySelector('input[name="sources"]'));
const toggleAll = document.getElementById('toggle-all-sources');
const search = document.getElementById('source-search');
const chips = [...document.querySelectorAll('.chips .chip')];
let kind = 'all';
function refresh() {{
  const shown = rows.filter(row => !row.hidden).map(row => row.querySelector('input[name="sources"]'));
  const checked = boxes.filter(box => box.checked).length;
  document.querySelectorAll('.selected-count').forEach(count => {{ count.textContent = checked; }});
  document.getElementById('selected-total').textContent = checked;
  document.querySelectorAll('.run-button').forEach(button => {{ button.disabled = checked === 0; }});
  toggleAll.checked = shown.length > 0 && shown.every(box => box.checked);
  toggleAll.indeterminate = shown.some(box => box.checked) && !toggleAll.checked;
}}
function applyFilter() {{
  const query = search.value.trim().toLowerCase();
  rows.forEach(row => {{
    row.hidden = (kind !== 'all' && row.dataset.kind !== kind)
      || (query !== '' && !row.dataset.search.includes(query));
  }});
  chips.forEach(chip => chip.classList.toggle('active', chip.dataset.kind === kind));
  refresh();
}}
toggleAll.addEventListener('change', () => {{
  rows.filter(row => !row.hidden).forEach(row => {{
    row.querySelector('input[name="sources"]').checked = toggleAll.checked;
  }});
  refresh();
}});
boxes.forEach(box => box.addEventListener('change', refresh));
search.addEventListener('input', applyFilter);
chips.forEach(chip => chip.addEventListener('click', () => {{ kind = chip.dataset.kind; applyFilter(); }}));
refresh();
</script>"""
    page = _page(
        "Управление",
        body,
        active="management",
        instruction=(
            "Четыре шага по кругу: подгрузить статьи → очистить от мусора → собрать "
            "сущности → разрешить персоны. Нажать можно только подсвеченный шаг."
        ),
        next_action="Галочки источников действуют на шаги 1 и 4; шаги 2 и 3 — на всю базу.",
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
    definitions = news_sources()
    selected = {item.name for item in definitions}
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
        selected = set(run.parameters.sources or selected)
    history = _recent_runs(registry)
    if run_id is None:
        run = history[0] if history else None
    return _management_page(
        db, selected=selected, run=run, history=history, state=current_state(registry)
    )


@router.post("/ui/management/run", response_model=None)
async def start_management_run(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Step 1: load and extract the selected sources."""
    return await _start(request, db, registry, "load")


@router.post("/ui/management/purge", response_model=None)
def start_management_purge(
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Step 2: delete the articles without a criminal case; the whole database."""
    return _start_whole_database(db, registry, "purge")


@router.post("/ui/management/entities", response_model=None)
def start_management_entities(
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Step 3: rebuild the person entities; the whole database."""
    return _start_whole_database(db, registry, "entities")


def _refused(
    db: Session, registry: OperationRegistry, warning: str, status_code: int, selected: set[str]
) -> HTMLResponse:
    return _management_page(
        db,
        selected=selected,
        warning=warning,
        history=_recent_runs(registry),
        state=current_state(registry),
        status_code=status_code,
    )


def _start_whole_database(
    db: Session, registry: OperationRegistry, mode: Literal["purge", "entities"]
) -> HTMLResponse | RedirectResponse:
    everything = {item.name for item in news_sources()}
    refusal = out_of_turn(current_state(registry), mode)
    if refusal is not None:
        return _refused(db, registry, refusal, 409, everything)
    try:
        run = registry.start(_OPERATION, OperationParameters(mode=mode))
    except OperationConflictError:
        # Another worker started a run between the check and the start.
        return _refused(db, registry, "Идёт другой запуск.", 409, everything)
    return RedirectResponse(f"/ui/management?run_id={run.id}", status_code=303)


async def _start(
    request: Request, db: Session, registry: OperationRegistry, mode: Literal["load"]
) -> HTMLResponse | RedirectResponse:
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    selected = list(dict.fromkeys(form.get("sources", [])))
    allowed = {item.name for item in news_sources()}
    refusal = out_of_turn(current_state(registry), mode)
    if refusal is not None:
        return _refused(db, registry, refusal, 409, set(selected) & allowed)
    if not selected:
        return _refused(db, registry, "Выберите хотя бы один новостной источник.", 400, set())
    if not set(selected) <= allowed:
        return _refused(
            db,
            registry,
            "В запросе есть неизвестный или не новостной источник.",
            400,
            set(selected) & allowed,
        )
    try:
        run = registry.start(_OPERATION, OperationParameters(sources=selected, mode=mode))
    except OperationConflictError:
        return _refused(db, registry, "Идёт другой запуск.", 409, set(selected))
    return RedirectResponse(f"/ui/management?run_id={run.id}", status_code=303)


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

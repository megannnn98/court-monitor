"""«Обзор», the home page: what is found, what is new, what waits for the operator, and
where the pipeline stands. The technical tables of the runs stay folded or on
«Управление»."""

from __future__ import annotations

from datetime import datetime
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from db.orm_models import EntityGroupPoliticsRecord, EntityGroupRecord, EntityGroupRoleRecord
from entities.politics import POLITICAL
from entities.roles import FIGURANT
from operator_console import OperationRegistry, OperationRun
from sources.source_registry import news_sources
from web.dependencies import get_db, get_operation_registry
from web.ui.dossier import VERDICT_LABELS
from web.ui.entities import display_name
from web.ui.funnel import funnel, funnel_html
from web.ui.layout import _page
from web.ui.management import _RUN_STATUS_BADGES, _RUN_STATUS_LABELS, _badge, _history
from web.ui.pipeline import OPERATION, STAGES, TITLES, PipelineState, current_state, stepper
from web.ui.workload import workload

router = APIRouter()

NEW_FIGURANTS = 10
SOURCE_ERRORS = 8

# The figurants whose first publication is the latest: the new cases.
_NEW_FIGURANTS = text(
    """
    SELECT g.key, g.name, first.first_at, po.verdict
    FROM entity_groups g
    JOIN entity_group_roles ro ON ro.group_id = g.id AND ro.role = :figurant
    LEFT JOIN entity_group_politics po ON po.group_id = g.id
    JOIN LATERAL (
        SELECT min(a.published_at) AS first_at FROM entity_group_mentions gm
        JOIN entity_mentions m ON m.id = gm.mention_id
        JOIN article_extraction_runs r ON r.id = m.extraction_run_id
        JOIN parsed_articles a ON a.id = r.article_id
        WHERE gm.group_id = g.id
    ) first ON true
    ORDER BY first.first_at DESC NULLS LAST, g.key
    LIMIT :limit
    """
)
_SOURCE_ERRORS = text(
    """
    SELECT r.source, i.stage, i.error_type, left(i.error_message, 240) AS message, i.created_at
    FROM monitoring_run_items i JOIN monitoring_runs r ON r.id = i.run_id
    WHERE i.status = 'failed'
    ORDER BY i.created_at DESC, i.id DESC
    LIMIT :limit
    """
)


def _day(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%d.%m.%Y") if moment else "—"


def _kpi(label: str, value: object, href: str, note: str = "") -> str:
    return (
        f'<a class="kpi" href="{href}"><span class="kpi-label">{escape(label)}</span>'
        f'<strong class="kpi-value">{value}</strong>'
        f"{f'<span class=kpi-note>{escape(note)}</span>' if note else ''}</a>"
    )


def _stages(runs: list[OperationRun], state: PipelineState) -> str:
    """The six steps: the latest run of each, and which one may run now."""
    latest: dict[str, OperationRun] = {}
    for each in runs:
        mode = each.parameters.mode or ""
        if mode in STAGES and mode not in latest:
            latest[mode] = each
    rows = []
    for number, stage in enumerate(STAGES, start=1):
        run = latest.get(stage)
        status = (
            _badge(_RUN_STATUS_LABELS[run.status], _RUN_STATUS_BADGES[run.status])
            if run is not None
            else '<span class="muted">не запускался</span>'
        )
        when = (
            f'<a href="/ui/management?run_id={run.id}">#{run.id}</a> {_day(run.created_at)}'
            if run is not None
            else ""
        )
        now = ' <span class="badge running">сейчас</span>' if stage == state.current else ""
        rows.append(
            f"<tr><td>{number}. {escape(TITLES[stage])}{now}</td><td>{status}</td><td>{when}</td></tr>"
        )
    return "".join(rows)


@router.get("/ui/overview", response_class=HTMLResponse)
def ui_overview(
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    work = workload(db)
    articles = db.scalar(text("SELECT count(*) FROM parsed_articles")) or 0
    people = db.scalar(select(func.count()).select_from(EntityGroupRecord)) or 0
    figurants = (
        db.scalar(
            select(func.count())
            .select_from(EntityGroupRoleRecord)
            .where(EntityGroupRoleRecord.role == FIGURANT)
        )
        or 0
    )
    result = (
        db.scalar(
            select(func.count())
            .select_from(EntityGroupPoliticsRecord)
            .where(EntityGroupPoliticsRecord.verdict == POLITICAL)
        )
        or 0
    )
    errors = db.execute(_SOURCE_ERRORS, {"limit": SOURCE_ERRORS}).all()
    new = db.execute(_NEW_FIGURANTS, {"figurant": FIGURANT, "limit": NEW_FIGURANTS}).all()
    state = current_state(registry)
    runs = registry.runs_of(OPERATION, limit=50)

    new_rows = "".join(
        f'<tr><td><a href="/ui/investigations/{quote(key)}">{escape(display_name(name))}</a></td>'
        f"<td>{_day(first_at)}</td>"
        f"<td>{_badge(VERDICT_LABELS.get(verdict, verdict), 'succeeded' if verdict == POLITICAL else '') if verdict else '<span class=muted>не оценено</span>'}</td></tr>"
        for key, name, first_at, verdict in new
    )
    error_rows = "".join(
        f"<tr><td>{escape(source or 'общий проход')}</td><td>{escape(stage or '—')}</td>"
        f"<td>{escape(error_type or '')}: {escape(message or '')}</td><td>{_day(created_at)}</td></tr>"
        for source, stage, error_type, message, created_at in errors
    )
    body = f"""<section class="kpis" aria-label="Итоговые показатели">
  {_kpi("Публикации", articles, "/ui/publications", "с уголовным делом")}
  {_kpi("Люди", people, "/ui/entities?figurants=all&rf=all", "выделено из упоминаний")}
  {_kpi("Фигуранты", figurants, "/ui/entities", "на них заведено дело")}
  {_kpi("Результат", result, "/ui/political", "политические уголовные дела")}
  {_kpi("Очередь", work.total, "/ui/queue", "ждут решения оператора")}
</section>
<div class="overview-grid">
<section class="band" aria-labelledby="new-title">
  <h2 id="new-title">Новые фигуранты</h2>
  <p class="muted">По дате первой публикации о человеке.</p>
  {
        f'''<table><caption class="visually-hidden">Новые фигуранты</caption>
  <thead><tr><th scope="col">Человек</th><th scope="col">Первая публикация</th>
  <th scope="col">Дело</th></tr></thead><tbody>{new_rows}</tbody></table>'''
        if new_rows
        else '<p class="empty">Фигурантов пока нет: шаг 5 не запускался.</p>'
    }
</section>
<section class="band" aria-labelledby="queue-title">
  <h2 id="queue-title">Очередь проверки</h2>
  <ul class="queue-summary">
    <li><a href="/ui/queue#pairs">Спорные совпадения людей</a> <strong>{work.pairs}</strong></li>
    <li><a href="/ui/queue#roles">Неясная роль в деле</a> <strong>{work.unclear_roles}</strong></li>
    <li><a href="/ui/queue#verdicts">Неясная политичность</a> <strong>{
        work.unclear_verdicts
    }</strong></li>
  </ul>
  {
        '<p class="empty">Очередь пуста.</p>'
        if not work.total
        else '<p><a class="button-link" href="/ui/queue">Разобрать очередь</a></p>'
    }
</section>
</div>
<section class="band" aria-labelledby="pipeline-title">
  <h2 id="pipeline-title">Цикл обработки</h2>
  <form method="post" action="/ui/management/run" class="run-bar">
    {stepper(state, len(news_sources()))}
  </form>
  <table><caption class="visually-hidden">Этапы</caption>
  <thead><tr><th scope="col">Этап</th><th scope="col">Последний запуск</th>
  <th scope="col">Когда</th></tr></thead><tbody>{_stages(runs, state)}</tbody></table>
  <details><summary>Последние запуски подробно</summary>{_history(runs[:10], None)}</details>
  <details><summary>Воронка отбора</summary>{funnel_html(funnel(db))}</details>
</section>
<section class="band" aria-labelledby="errors-title">
  <h2 id="errors-title">Ошибки источников</h2>
  {
        f'''<table><caption class="visually-hidden">Последние ошибки</caption>
  <thead><tr><th scope="col">Источник</th><th scope="col">Этап</th><th scope="col">Ошибка</th>
  <th scope="col">Когда</th></tr></thead><tbody>{error_rows}</tbody></table>'''
        if error_rows
        else '<p class="empty">Ошибок нет.</p>'
    }
</section>"""
    return _page(
        "Обзор",
        body,
        active="overview",
        instruction="Что найдено, что нового и что ждёт решения.",
        next_action="Откройте нового фигуранта или разберите очередь; цикл обработки — кнопкой текущего этапа.",
        db=db,
    )

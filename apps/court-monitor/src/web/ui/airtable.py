"""«Справочники»: the lists a person keeps by hand, and the button that brings them in.

Airtable is where the lists are written; PostgreSQL is what the pipeline reads. The
button runs one sync and shows what each of the four lists did, without leaving the
page — and says plainly which list failed, so one broken table does not read as
«nothing happened».

Records arrive one of two ways, and the page says which before the button is pressed:
through the Airtable API, or from CSVs the operator exported and dropped into
`airtable-import/` (for a base that is only readable in a browser).
"""

from __future__ import annotations

import json
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from airtable.config import AirtableConfigurationError
from airtable.files import FileTableClient, ImportSettings
from airtable.models import MODE_FILES, TABLE_LABELS, TABLES
from airtable.service import (
    AirtableSyncAlreadyRunningError,
    build_sync_service,
    build_sync_source,
)
from application import build_analysis_service
from orchestrator.operations import (
    OperationConflictError,
    OperationParameters,
    OperationRegistry,
)
from web.dependencies import get_db, get_operation_registry, session_factory_for
from web.ui.layout import _page
from web.views.airtable import (
    _MODE_NOTES,
    SYNC_URL,
    _inventory,
    _source_column_title,
    _source_names,
)
from web.views.officials_list import REFERENCE_URL as OFFICIALS_URL

router = APIRouter()


def _official_html(db: Session) -> str:
    """Which published list the database is working against right now."""
    session_factory = session_factory_for(db)
    summary = build_analysis_service(session_factory).latest_snapshot()
    if summary is None:
        return '<p class="warning">Перечень ещё не загружен: сверить людей с ним не с чем.</p>'
    return (
        f'<p class="muted">Снимок #{summary.snapshot_id} от '
        f"{summary.snapshot_date.astimezone().strftime('%d.%m.%Y %H:%M')}, "
        f"записей {summary.entry_count:,}, сверено {summary.match_count:,}.</p>"
    )


@router.get("/ui/airtable", response_class=HTMLResponse)
def ui_airtable(db: Session = Depends(get_db)) -> HTMLResponse:  # noqa: B008
    try:
        source = build_sync_source()
        configured, reason = True, ""
    except AirtableConfigurationError as exc:
        source, configured, reason = None, False, str(exc)
    present = FileTableClient(ImportSettings.from_env()).present() if configured else {}
    names = _source_names(source, present)
    rows = "".join(
        "<tr>"
        # The officials list is the one edited here rather than read from Airtable, so
        # its name is a link to it rather than a label.
        + (
            f'<td><a href="{OFFICIALS_URL}">{escape(label)}</a></td>'
            if name == "officials"
            else f"<td>{escape(label)}</td>"
        )
        + f'<td class="num">{count}</td>'
        + f"<td>{escape(names.get(name) or '—')}</td>"
        + "</tr>"
        for name, label, count in _inventory(db)
    )
    button = (
        '<button id="sync-button" type="button">Синхронизировать Airtable</button>'
        if configured
        else '<button type="button" disabled>Синхронизировать Airtable</button>'
    )
    notice = (
        "" if configured else f'<p class="warning">Синхронизация недоступна: {escape(reason)}</p>'
    )
    mode_note = f'<p class="muted">{escape(_MODE_NOTES[source.mode])}</p>' if source else ""
    official_html = _official_html(db)
    missing = ""
    if source is not None and source.mode == MODE_FILES:
        absent = [TABLE_LABELS[name] for name in TABLES if name not in present]
        if absent:
            missing = (
                '<p class="muted">Файла нет — список останется как был: '
                f"{escape(', '.join(absent))}.</p>"
            )
    # The page hands the script its endpoint and the list order; no secret is among it.
    # A `application/json` block is read as text, so it is not HTML-escaped — only the
    # one sequence that could close the tag early is neutralised.
    config_json = json.dumps(
        {"url": SYNC_URL, "labels": TABLE_LABELS, "order": list(TABLES)}, ensure_ascii=False
    ).replace("<", "\\u003c")
    body = f"""{notice}{mode_note}{missing}
<section class="band" aria-labelledby="sync-title">
  <h2 id="sync-title">Синхронизация справочников</h2>
  <p class="muted">Airtable — внешняя админка, PostgreSQL — рабочее хранилище. Кнопка
  переносит четыре справочника в базу; обработка статей к Airtable не обращается.</p>
  {button}
  <p id="sync-status" class="muted" role="status" aria-live="polite">синхронизация ещё не выполнялась</p>
  <div id="sync-result"></div>
</section>
<section class="band" aria-labelledby="official-title">
  <h2 id="official-title">Официальный перечень РФМ (fedsfm.ru)</h2>
  {official_html}
  <p>Перечень публикует fedsfm.ru, и система берёт его оттуда сама. Кнопка скачивает
  опубликованный список, <strong>создаёт новый снимок только если перечень
  изменился</strong> (список опознаётся по содержимому, поэтому повторное нажатие
  ничего не создаёт) и пересверяет людей с перечнем заново — у нового снимка нет
  результатов сверки, и «Кандидаты» до неё показывали бы пустоту.</p>
  <p><form method="post" action="/ui/airtable/rosfin"><button type="submit">Обновить перечень и сверить с РФМ</button></form></p>
  <p class="muted">Скачивание и сверка идут в фоне, поэтому после нажатия вы
  попадёте на карточку запуска: <a href="/ui/runs">«Журнал запусков»</a>. То же
  делает и CLI-команда <code>check-entities-rosfin</code>.</p>
</section>
<section class="band" aria-labelledby="stock-title">
  <h2 id="stock-title">Что сейчас в базе</h2>
  <table><thead><tr><th>Справочник</th><th>Записей</th>
  <th>{_source_column_title(source)}</th>
  </tr></thead><tbody>{rows}</tbody></table>
</section>
<script type="application/json" id="airtable-sync-config">
{config_json}
</script>
<script src="/static/airtable-sync.js" defer></script>"""
    return _page(
        "База Airtable",
        body,
        active="airtable",
        instruction=(
            "Источники, найденные люди, должностные лица и статьи ведутся в Airtable. "
            "Кнопка переносит их в PostgreSQL; дальше система работает только с базой. "
            "Официальный перечень Росфинмониторинга — отдельный раздел ниже: он "
            "публикуется на fedsfm.ru и ни с чем из Airtable не смешивается."
        ),
        db=db,
    )


@router.post("/ui/airtable/sync", response_model=None)
def sync_from_ui(db: Session = Depends(get_db)) -> RedirectResponse:  # noqa: B008
    """The button without JavaScript: run the sync, come back to the page."""
    try:
        service = build_sync_service(session_factory_for(db))
    except AirtableConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        report = service.sync()
    except AirtableSyncAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(f"/ui/airtable?status={report.status}", status_code=303)


@router.post("/ui/airtable/rosfin", response_model=None)
def start_rosfin(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Refresh the published Rosfinmonitoring list and re-check every entity against it.

    The button starts the operation that already exists — `check-entities-rosfin`, the
    same one step 5 runs and the CLI exposes — rather than downloading anything here. Two
    reasons, and both are the point of this route: the download takes longer than an HTTP
    request may last, and a second downloader here would be a second thing to keep
    working, a second thing to be wrong about what counts as the published list, and a
    second set of answers about the 403 and the maintenance page.

    **Why the order check is not applied here.** `start_step` asks
    `out_of_turn` first, which requires that the step be the pipeline's next one. That
    works for the five numbered stages but not for `rosfin`: `pipeline_current` answers
    from the stage list, and `rosfin` is not in it — it reports `figurants` for a run of
    this mode. So through that helper `rosfin` is refused with 409 every time, before any
    start is attempted, and a button calling it would never work.

    What actually keeps a second run from starting is the registry itself: at most one
    live run per operation, enforced in the database, and `start` says so rather than
    queueing. That is the guarantee this route needs — two runs would fight over the
    snapshot — and it is the one that survives a restart or a second API process. So the
    conflict is handled here and the order is not: the published list is checked on demand
    whenever the operator asks, not because the step before it happened to run.
    """
    from web.ui.management import _refused, _started_at
    from web.views.management import _OPERATION

    try:
        run = registry.start(_OPERATION, OperationParameters(mode="rosfin"))
    except OperationConflictError:
        # Something is running. The management page says so, with the run, rather than
        # this route inventing a second wording of the same refusal.
        return _refused(db, registry, "Идёт другой запуск.", 409)
    return RedirectResponse(_started_at(request, run.id), status_code=303)

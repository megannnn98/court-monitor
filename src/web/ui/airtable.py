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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from airtable.config import AirtableConfigurationError
from airtable.files import FileTableClient, ImportSettings
from airtable.models import TABLE_LABELS, TABLES
from airtable.repository import RFM_SOURCE_URL
from airtable.service import (
    MODE_API,
    MODE_FILES,
    MODE_SHARE,
    AirtableSyncAlreadyRunningError,
    build_sync_service,
    build_sync_source,
)
from db.orm_models import (
    AirtableKnownPersonRecord,
    CriminalArticleRecord,
    ExcludedPersonRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
    Source,
)
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup
from web.dependencies import get_db, session_factory_for
from web.ui.layout import _page
from web.ui.officials_list import REFERENCE_URL as OFFICIALS_URL

router = APIRouter()

SYNC_URL = "/api/admin/airtable/sync"
IMPORT_URL = "/api/admin/rosfinmonitoring/import"

# Shown in place of the Airtable table name when records come from a file.
_FILE_COLUMN = "Файл"
_MODE_NOTES = {
    MODE_API: "Список читается из Airtable по API — кнопка сама забирает свежие данные.",
    MODE_SHARE: (
        "Список читается по публичным ссылкам Airtable: кнопка сама забирает свежие "
        "данные, без токена и без выгрузки вручную. Списка, для которого ссылки нет, "
        "как не было: он пропускается."
    ),
    MODE_FILES: (
        "Список читается из файлов: выгрузите таблицу из Airtable и положите CSV в "
        "папку airtable-import/ (в контейнере — /import). Списка, для которого файла нет, "
        "как не было: она пропускается."
    ),
}


def _row_count(db: Session, model: type[Any], *conditions: Any) -> int:
    query = select(func.count()).select_from(model)
    for condition in conditions:
        query = query.where(condition)
    return int(db.scalar(query) or 0)


def _inventory(db: Session) -> list[tuple[str, str, int]]:
    """(list, label, rows in PostgreSQL) for the four lists."""
    return [
        ("sources", TABLE_LABELS["sources"], _row_count(db, Source, Source.active.is_(True))),
        ("rfm_persons", TABLE_LABELS["rfm_persons"], _rfm_count(db)),
        (
            "known_persons",
            TABLE_LABELS["known_persons"],
            _row_count(db, AirtableKnownPersonRecord),
        ),
        (
            "officials",
            TABLE_LABELS["officials"],
            _row_count(db, ExcludedPersonRecord, ExcludedPersonRecord.active.is_(True)),
        ),
        (
            "articles",
            TABLE_LABELS["articles"],
            _row_count(db, CriminalArticleRecord, CriminalArticleRecord.active.is_(True)),
        ),
    ]


def _rfm_count(db: Session) -> int:
    """The entries of our own Airtable-sourced snapshot."""
    snapshot_id = db.scalar(
        select(RosfinmonitoringSnapshotRecord.id)
        .where(RosfinmonitoringSnapshotRecord.source_url == RFM_SOURCE_URL)
        .limit(1)
    )
    if snapshot_id is None:
        return 0
    return int(
        db.scalar(
            select(func.count())
            .select_from(RosfinmonitoringEntryRecord)
            .where(RosfinmonitoringEntryRecord.snapshot_id == snapshot_id)
        )
        or 0
    )


def _official_html(db: Session) -> str:
    """Which published list the database is working against right now."""
    session_factory = session_factory_for(db)
    summary = SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory).latest_imported_snapshot()
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
  <h2 id="official-title">Перечень Росфинмониторинга</h2>
  {official_html}
  <p class="muted">Обычно список скачивается сам на шаге 5. Когда сайт недоступен,
  сохраните страницу перечня и загрузите файл здесь — он станет новым снимком.
  Тот же файл дважды ничего не меняет: список опознаётся по содержимому.</p>
  <p class="muted">После загрузки нужно <strong>сверить с РФМ</strong> заново: у нового
  снимка нет результатов сверки, и «Кандидаты» до этого покажут пустоту. Это делает шаг 5
  на странице <a href="/ui/runs">«Журнал запусков»</a>.</p>
  <p><label>Файл перечня (.html, .xml, .json, .csv)
    <input id="official-file" type="file" accept=".html,.htm,.xml,.json,.csv"
      data-url="{IMPORT_URL}"></label>
    <button id="import-button" type="button">Загрузить перечень</button></p>
  <p id="import-status" class="muted" role="status" aria-live="polite"></p>
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
        "Справочники",
        body,
        active="airtable",
        instruction=(
            "Источники, перечень Росфинмониторинга, найденные люди и список исключений "
            "ведутся в Airtable. Кнопка переносит их в PostgreSQL; дальше система работает "
            "только с базой. Официальный перечень РФМ — отдельный раздел ниже."
        ),
        db=db,
    )


def _source_column_title(source: Any) -> str:
    """What the third column should be called, given where the lists are read from."""
    if source is not None and source.mode == MODE_FILES:
        return "Файл"
    if source is not None and source.mode == MODE_SHARE:
        return "Публичная ссылка"
    return "Таблица Airtable"


def _source_names(source: Any, present: dict[str, Any]) -> dict[str, str]:
    """What each list is read from, as the page should name it: the Airtable table in API
    mode, the file that was found in file mode."""
    if source is None:
        return {}
    if source.mode == MODE_API:
        return {name: source.table_for(name) for name in TABLES}
    if source.mode == MODE_SHARE:
        links = getattr(source.client, "links", {})
        return {name: link for name, link in links.items()}
    return {name: path.name for name, path in present.items()}


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

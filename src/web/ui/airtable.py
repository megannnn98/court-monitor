"""«Справочники»: the lists a person keeps by hand, and the button that brings them in.

Airtable is where the lists are written; PostgreSQL is what the pipeline reads. The
button runs one sync and shows what each of the four lists did, without leaving the
page — and says plainly which list failed, so one broken table does not read as
«nothing happened».
"""

from __future__ import annotations

import json
from html import escape
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from airtable.config import AirtableConfigurationError, AirtableSettings
from airtable.models import TABLE_LABELS, TABLES
from airtable.repository import RFM_SOURCE_URL
from airtable.service import AirtableSyncAlreadyRunningError, AirtableSyncService
from db.orm_models import (
    AirtableKnownPersonRecord,
    ExcludedPersonRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
    Source,
)
from web.dependencies import get_db, session_factory_for
from web.ui.layout import _page

router = APIRouter()

SYNC_URL = "/api/admin/airtable/sync"


def _row_count(db: Session, model: type[Any], *conditions: Any) -> int:
    query = select(func.count()).select_from(model)
    for condition in conditions:
        query = query.where(condition)
    return int(db.scalar(query) or 0)


def _inventory(db: Session) -> list[tuple[str, str, int]]:
    """(table, label, rows in PostgreSQL) for the four lists."""
    return [
        (
            "sources",
            TABLE_LABELS["sources"],
            _row_count(db, Source, Source.active.is_(True)),
        ),
        ("rfm_persons", TABLE_LABELS["rfm_persons"], _rfm_count(db)),
        ("known_persons", TABLE_LABELS["known_persons"], _row_count(db, AirtableKnownPersonRecord)),
        (
            "excluded_persons",
            TABLE_LABELS["excluded_persons"],
            _row_count(db, ExcludedPersonRecord, ExcludedPersonRecord.active.is_(True)),
        ),
    ]


def _table_names(settings: AirtableSettings | None) -> dict[str, str]:
    """The Airtable table each list is read from, so the page names the real base."""
    if settings is None:
        return {}
    return {
        "sources": settings.sources_table,
        "rfm_persons": settings.rfm_persons_table,
        "known_persons": settings.known_persons_table,
        "excluded_persons": settings.excluded_persons_table,
    }


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


def _last_sync() -> str:
    return "синхронизация ещё не выполнялась"


@router.get("/ui/airtable", response_class=HTMLResponse)
def ui_airtable(db: Session = Depends(get_db)) -> HTMLResponse:  # noqa: B008
    settings: AirtableSettings | None
    try:
        settings = AirtableSettings.from_env()
        configured, reason = True, ""
    except AirtableConfigurationError as exc:
        settings, configured, reason = None, False, str(exc)
    names = _table_names(settings)
    rows = "".join(
        f"<tr><td>{escape(label)}</td>"
        f'<td class="num">{count}</td>'
        f"<td>{escape(names.get(table) or '—')}</td></tr>"
        for table, label, count in _inventory(db)
    )
    button = (
        '<button id="sync-button" type="button">Синхронизировать Airtable</button>'
        if configured
        else '<button type="button" disabled>Синхронизировать Airtable</button>'
    )
    notice = "" if configured else f'<p class="warning">Airtable не настроен: {escape(reason)}</p>'
    # The page hands the script its endpoint and the table order; no secret is among it.
    # A `application/json` block is read as text, so it is not HTML-escaped — only the
    # one sequence that could close the tag early is neutralised.
    config_json = json.dumps(
        {"url": SYNC_URL, "labels": TABLE_LABELS, "order": list(TABLES)}, ensure_ascii=False
    ).replace("<", "\\u003c")
    body = f"""{notice}
<section class="band" aria-labelledby="sync-title">
  <h2 id="sync-title">Синхронизация справочников</h2>
  <p class="muted">Airtable — внешняя админка, PostgreSQL — рабочее хранилище. Кнопка
  переносит четыре справочника в базу; обработка статей к Airtable не обращается.</p>
  {button}
  <p id="sync-status" class="muted" role="status" aria-live="polite">{escape(_last_sync())}</p>
  <div id="sync-result"></div>
</section>
<section class="band" aria-labelledby="stock-title">
  <h2 id="stock-title">Что сейчас в базе</h2>
  <table><thead><tr><th>Справочник</th><th>Записей</th><th>Таблица Airtable</th></tr></thead>
  <tbody>{rows}</tbody></table>
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
            "ведятся в Airtable. Кнопка переносит их в PostgreSQL; дальше система работает "
            "только с базой."
        ),
        db=db,
    )


@router.post("/ui/airtable/sync", response_model=None)
def sync_from_ui(db: Session = Depends(get_db)) -> RedirectResponse:  # noqa: B008
    """The button without JavaScript: run the sync, come back to the page."""
    try:
        settings = AirtableSettings.from_env()
    except AirtableConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        report = AirtableSyncService(session_factory_for(db), settings).sync()
    except AirtableSyncAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(f"/ui/airtable?status={report.status}", status_code=303)

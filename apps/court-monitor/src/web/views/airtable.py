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

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from airtable.config import AirtableConfigurationError
from airtable.files import FileTableClient, ImportSettings
from airtable.models import MODE_API, MODE_FILES, MODE_SHARE, TABLE_LABELS, TABLES
from airtable.service import (
    build_sync_source,
)
from analysis import read as analysis_read
from application import build_analysis_service
from ingestion import read as ingestion_read
from processing.roles import exclusion_list
from web.dependencies import session_factory_for

SYNC_URL = "/api/admin/airtable/sync"

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


def _inventory(db: Session) -> list[tuple[str, str, int]]:
    """(list, label, rows in PostgreSQL) for the four lists."""
    known, articles = analysis_read.reference_counts(db)
    return [
        ("sources", TABLE_LABELS["sources"], ingestion_read.active_source_count(db)),
        ("known_persons", TABLE_LABELS["known_persons"], known),
        ("officials", TABLE_LABELS["officials"], exclusion_list.active_count(db)),
        ("articles", TABLE_LABELS["articles"], articles),
    ]


@dataclass(frozen=True)
class Overview:
    """Where the lists come from and what PostgreSQL holds; read by this page and the API."""

    configured: bool
    reason: str
    mode_note: str
    # The lists whose file is missing, in file mode: they stay as they were.
    missing: list[str]
    source_column: str
    # (list, label, rows in PostgreSQL, what it is read from)
    inventory: list[tuple[str, str, int, str]]


def airtable_overview(db: Session) -> Overview:
    try:
        source = build_sync_source()
        configured, reason = True, ""
    except AirtableConfigurationError as exc:
        source, configured, reason = None, False, str(exc)
    present = FileTableClient(ImportSettings.from_env()).present() if configured else {}
    names = _source_names(source, present)
    missing = (
        [TABLE_LABELS[name] for name in TABLES if name not in present]
        if source is not None and source.mode == MODE_FILES
        else []
    )
    return Overview(
        configured=configured,
        reason=reason,
        mode_note=_MODE_NOTES[source.mode] if source else "",
        missing=missing,
        source_column=_source_column_title(source),
        inventory=[
            (name, label, count, names.get(name) or "") for name, label, count in _inventory(db)
        ],
    )


def official_summary(db: Session) -> Any:
    """The published list the database works against now; None before the first."""
    return build_analysis_service(session_factory_for(db)).latest_snapshot()


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

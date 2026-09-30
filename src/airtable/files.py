"""Reading the four lists from exported files instead of from the Airtable API.

A public Airtable view can be exported to CSV without any token, and for a list that
changes rarely the export is a ten-second job. This reader implements the same
`AirtableClient` protocol as the HTTP client, so the sync itself is one code path
whichever way the records arrive.

Two rules matter more than the parsing:

- a **missing** file is skipped, not read as an empty list, so exporting only the
  Rosfinmonitoring file leaves the other three alone;
- a file that holds **no rows** is refused, because reading it as an empty list would
  mean "the list is now empty" — and for `rfm_persons` that would delete the whole
  snapshot. One careless export must not wipe it.
"""

from __future__ import annotations

import csv
import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from airtable.client import AirtableError, AirtableRecord
from airtable.models import TABLES

logger = logging.getLogger("airtable")

# Where the operator drops the files. A directory in the repository, bind-mounted into
# the container: an unmounted path would silently look empty from inside Docker.
DEFAULT_IMPORT_DIR = Path("airtable-import")
CONTAINER_IMPORT_DIR = Path("/import")
IMPORT_DIR_ENV = "AIRTABLE_IMPORT_DIR"

# The name a file carries for each list: `rfm_persons.csv` is the Rosfinmonitoring one.
FILE_STEM = dict(zip(TABLES, TABLES, strict=True))

# Columns that carry a checkbox. A CSV has no checkbox type, so the value is read by
# name and coerced; any other column is left as written, so a "0" in a numeric column
# never turns into False.
_BOOLEAN_COLUMNS = frozenset(
    {"active", "enabled", "активен", "активна", "активно", "включён", "включена", "работает"}
)
_TRUE = frozenset({"true", "1", "yes", "y", "да", "истина", "x", "+", "вкл"})
_FALSE = frozenset({"false", "0", "no", "n", "нет", "ложь", "-", "выкл", ""})

# The column that identifies a person, per list. It makes the synthesized id stable, so
# a second import of the same file reports every row as unchanged.
_KEY_COLUMN = {
    "sources": ("base_url", "Base URL", "url", "URL", "Ссылка", "Адрес"),
    "rfm_persons": ("full_name", "Full name", "Name", "ФИО", "Имя", "Фамилия Имя"),
    "known_persons": ("full_name", "Full name", "Name", "ФИО", "Имя", "Фамилия Имя"),
    "excluded_persons": ("full_name", "Full name", "Name", "ФИО", "Имя", "Фамилия Имя"),
}


@dataclass(frozen=True)
class ImportSettings:
    directory: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ImportSettings:
        import os

        env = os.environ if env is None else env
        raw = (env.get(IMPORT_DIR_ENV) or "").strip()
        return cls(directory=Path(raw) if raw else DEFAULT_IMPORT_DIR)


class FileTableClient:
    """The four lists as CSVs on disk.

    Same interface as `HttpAirtableClient`, so `AirtableSyncService` cannot tell the
    difference: it names a list and gets records back.
    """

    def __init__(self, settings: ImportSettings) -> None:
        self._settings = settings

    @property
    def directory(self) -> Path:
        return self._settings.directory

    def present(self) -> dict[str, Path]:
        """The files that are actually there, per list. A list without a file is skipped."""
        found: dict[str, Path] = {}
        for table in TABLES:
            path = self.path_for(table)
            if path.is_file():
                found[table] = path
        return found

    def path_for(self, table: str) -> Path:
        return self._settings.directory / f"{FILE_STEM[table]}.csv"

    def list_records(self, table: str) -> list[AirtableRecord]:
        """Records of one list. Raises `AirtableError` on anything unreadable."""
        path = self.path_for(table)
        if not path.is_file():
            raise FileNotFoundError(str(path))
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            raise AirtableError(f"файл не читается: {path} ({exc})", table=table) from exc
        except UnicodeDecodeError as exc:
            raise AirtableError(f"файл не в UTF-8: {path} ({exc})", table=table) from exc
        if not rows:
            # The dangerous case: a header with no rows means "the list is now empty",
            # and for the Rosfinmonitoring list that would delete the snapshot. Refused.
            raise AirtableError(
                f"файл {path.name} пуст (только заголовок) — список не тронут", table=table
            )
        logger.info(
            "event=airtable_import_read table=%s file=%s rows=%d", table, path.name, len(rows)
        )
        return [self._record(table, row) for row in rows]

    def _record(self, table: str, row: Mapping[str, str | None]) -> AirtableRecord:
        fields: dict[str, str | bool] = {}
        for raw_name, value in row.items():
            if raw_name is None:
                continue
            name = raw_name.strip()
            if not name:
                continue
            text = (value or "").strip()
            fields[name] = _as_boolean(name, text) if name.lower() in _BOOLEAN_COLUMNS else text
        return AirtableRecord(id=_synthetic_id(table, fields), fields=fields)


def _as_boolean(name: str, text: str) -> bool:
    """A checkbox column's value read by name. Anything unrecognized is False, which is
    the safe direction: a person is not silently treated as active."""
    if text.lower() in _TRUE:
        return True
    if text.lower() in _FALSE:
        return False
    logger.warning("event=airtable_import_bad_checkbox column=%s value=%s", name, text)
    return False


def _synthetic_id(table: str, fields: Mapping[str, object]) -> str:
    """A stable stand-in for the Airtable record id, so a repeated import of the same file
    is a no-op rather than a second copy of everything.

    Derived from the column that identifies the person in that list: the address for a
    source, the name (with the birth date, which tells namesakes apart) for a person.
    Renaming a person therefore reads as a new row — the honest reading of a file that
    says nothing about identity.
    """
    parts: list[str] = []
    for column in _KEY_COLUMN[table]:
        value = fields.get(column)
        if isinstance(value, str) and value.strip():
            parts.append(f"{column}={value.strip().lower().replace('ё', 'е')}")
    if table == "rfm_persons":
        for column in ("birth_date", "Дата рождения", "Birth date"):
            value = fields.get(column)
            if isinstance(value, str) and value.strip():
                parts.append(f"birth={value.strip()}")
                break
    key = "|".join(parts) if parts else "|".join(sorted(f"{k}={v}" for k, v in fields.items()))
    digest = hashlib.sha256(f"{table}|{key}".encode()).hexdigest()[:32]
    return f"file:{digest}"

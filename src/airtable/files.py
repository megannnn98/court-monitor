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
import re
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
    {
        "active",
        "enabled",
        "активен",
        "активна",
        "активно",
        "включён",
        "включена",
        "работает",
        # Airtable's own column for the membership flag, in the base it was curated in.
        "✦росфинмониторинг",
        "росфинмониторинг",
        "✦активен",
    }
)
_TRUE = frozenset({"true", "1", "yes", "y", "да", "истина", "x", "+", "вкл", "☑", "✓"})
_FALSE = frozenset({"false", "0", "no", "n", "нет", "ложь", "-", "выкл", "", "☐", "—"})

# Columns whose text is a date. Airtable writes dates in the locale of the view, so an
# export from an English view is "July 12, 1980" or "12 July 1980" — neither of which
# the pipeline's own date parser reads. Left alone, every birth date would arrive as
# NULL and namesakes would become indistinguishable.
_DATE_COLUMNS = frozenset(
    {
        "birth_date",
        "birth date",
        "дата рождения",
        "дата включения в список рфм",
        "дата исключения из списка рфм",
        "inclusion_date",
        "дата рождения преследуемого",
    }
)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}  # fmt: skip
# "July 12, 1980" and "12 July 1980": the two orders Airtable's export uses.
_ENGLISH_DATE = re.compile(r"^(?P<month>[A-Za-zа-яё]+)\.?\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})$")
_DAY_FIRST = re.compile(r"^(?P<day>\d{1,2})\.?\s+(?P<month>[A-Za-zа-яё]+),?\s+(?P<year>\d{4})$")
_ISO_LIKE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


# The column that identifies a person, per list. It makes the synthesized id stable, so
# a second import of the same file reports every row as unchanged.
_KEY_COLUMN = {
    "sources": ("base_url", "Base URL", "url", "URL", "Ссылка", "Адрес"),
    "rfm_persons": ("full_name", "Full name", "Name", "ФИО", "Имя", "Фамилия Имя"),
    "known_persons": ("full_name", "Full name", "Name", "ФИО", "Имя", "Фамилия Имя"),
    "officials": ("full_name", "Full name", "Name", "ФИО", "Имя", "Фамилия Имя", "Должность"),
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
            if name.lower() in _BOOLEAN_COLUMNS:
                fields[name] = _as_boolean(name, text)
            elif name.lower() in _DATE_COLUMNS:
                fields[name] = _as_date(text)
            else:
                fields[name] = text
        return AirtableRecord(id=_synthetic_id(table, fields), fields=fields)


def _as_boolean(name: str, text: str) -> bool:
    """A checkbox column's value read by name.

    Airtable writes a checkbox as `1` / `0`, and in some exports as
    `1 checked out of 1`. Anything unrecognized is False, which is the safe direction: a
    person is not silently treated as active.
    """
    lowered = text.lower()
    if lowered in _TRUE or lowered in _FALSE:
        return lowered in _TRUE
    checked = re.match(r"^(\d+)\s+checked out of\s+\d+$", lowered)
    if checked:
        return checked.group(1) != "0"
    logger.warning("event=airtable_import_bad_checkbox column=%s value=%s", name, text)
    return False


def _as_date(text: str) -> str:
    """A date column, rewritten into `YYYY-MM-DD` only when it is in a form the pipeline
    does not already read.

    Airtable writes dates in the locale of its view, so an export from an English one is
    `July 12, 1980` or `12 July 1980` — neither of which the pipeline's own parser
    understands. Everything else is passed through untouched: the parser already reads
    `ДД.ММ.ГГГГ` and `ГГГГ-ММ-ДД`, and guessing at the rest would be worse than leaving
    a value the parser will simply refuse.
    """
    if not text or _ISO_LIKE.match(text):
        return text
    for pattern in (_ENGLISH_DATE, _DAY_FIRST):
        if not (found := pattern.match(text)):
            continue
        month = _MONTHS.get(found.group("month").lower().replace("ё", "е"))
        if month is None:
            break
        return f"{found.group('year')}-{month:02d}-{int(found.group('day')):02d}"
    return text


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

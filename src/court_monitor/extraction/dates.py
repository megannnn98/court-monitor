"""Date parsing for Russian-language court texts.

Handles:
  * long form:  ``2 апреля 2026``, ``02 апреля 2026 г.``
  * numeric:    ``02.04.2026``, ``02/04/2026``
  * ISO:        ``2026-04-02``
Does NOT invent missing dates — returns ``None`` on no match.
"""

from __future__ import annotations

import re
from datetime import date, datetime

_MONTHS_RU = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
    # nominative fallbacks (less common in prose, but tolerated)
    "январь": 1,
    "февраль": 2,
    "март": 3,
    "апрель": 4,
    "май": 5,
    "июнь": 6,
    "июль": 7,
    "август": 8,
    "сентябрь": 9,
    "октябрь": 10,
    "ноябрь": 11,
    "декабрь": 12,
}

_LONG_RE = re.compile(
    r"\b(\d{1,2})\s+([а-яёА-ЯЁ]+)(?:\s+(\d{4}))?(?:\s*г\.?)?",
    re.IGNORECASE,
)
_NUMERIC_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def parse_russian_date(text: str) -> date | None:
    if not text:
        return None
    candidate = _try_iso(text)
    if candidate is not None:
        return candidate
    candidate = _try_long(text)
    if candidate is not None:
        return candidate
    return _try_numeric(text)


def _try_iso(text: str) -> date | None:
    m = _ISO_RE.search(text)
    if not m:
        return None
    return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _try_long(text: str) -> date | None:
    for m in _LONG_RE.finditer(text):
        day = int(m.group(1))
        month = _MONTHS_RU.get(m.group(2).lower())
        if month is None:
            continue
        year = int(m.group(3)) if m.group(3) else None
        if year is None:
            continue
        d = _safe_date(year, month, day)
        if d is not None:
            return d
    return None


def _try_numeric(text: str) -> date | None:
    m = _NUMERIC_RE.search(text)
    if not m:
        return None
    day = int(m.group(1))
    month = int(m.group(2))
    year = int(m.group(3))
    if year < 100:
        year += 2000 if year < 70 else 1900
    return _safe_date(year, month, day)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def first_datetime(text: str) -> datetime | None:
    d = parse_russian_date(text)
    return datetime(d.year, d.month, d.day) if d is not None else None

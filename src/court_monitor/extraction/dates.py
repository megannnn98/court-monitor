"""Date parsing for Russian-language court texts.

Handles:
  * long form:  ``2 апреля 2026``, ``02 апреля 2026 г.``
  * numeric:    ``02.04.2026``, ``02/04/2026``
  * ISO:        ``2026-04-02``
Does NOT invent missing dates — returns ``None`` on no match.

``extract_dates()`` returns ExtractedFactDTOs with normalized value,
original string, context, and optional date type.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from court_monitor.domain.facts import ExtractedFactDTO
from court_monitor.domain.models import VerificationStatus

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

# Date type hints from context
_DATE_TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:вынес[аеои]\s+)?приговор", re.IGNORECASE), "verdict_date"),
    (re.compile(r"постановлени[ея]", re.IGNORECASE), "ruling_date"),
    (re.compile(r"заседани[ея]", re.IGNORECASE), "hearing_date"),
    (re.compile(r"вступ[аи]л[аои]?\s+в\s+силу", re.IGNORECASE), "effective_date"),
    (re.compile(r"обжалован[аиео]*", re.IGNORECASE), "appeal_deadline"),
    (re.compile(r"задержан[аиео]*", re.IGNORECASE), "detention_date"),
    (re.compile(r"арестован[аиео]*", re.IGNORECASE), "arrest_date"),
]


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


def extract_dates(text: str, *, source_url: str | None = None) -> list[ExtractedFactDTO]:
    """Extract all dates from text as ExtractedFactDTOs."""
    if not text:
        return []

    out: list[ExtractedFactDTO] = []
    seen_spans: set[tuple[int, int]] = set()

    # ISO dates
    for m in _ISO_RE.finditer(text):
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d is None:
            continue
        _add_date(out, d, m.group(0), m.start(), m.end(),
                  text=text, source_url=source_url, seen_spans=seen_spans)

    # Long form dates
    for m in _LONG_RE.finditer(text):
        day = int(m.group(1))
        month = _MONTHS_RU.get(m.group(2).lower())
        if month is None:
            continue
        year = int(m.group(3)) if m.group(3) else None
        if year is None:
            continue
        d = _safe_date(year, month, day)
        if d is None:
            continue
        _add_date(out, d, m.group(0), m.start(), m.end(),
                  text=text, source_url=source_url, seen_spans=seen_spans)

    # Numeric dates
    for m in _NUMERIC_RE.finditer(text):
        day = int(m.group(1))
        month = int(m.group(2))
        year = int(m.group(3))
        if year < 100:
            year += 2000 if year < 70 else 1900
        d = _safe_date(year, month, day)
        if d is None:
            continue
        _add_date(out, d, m.group(0), m.start(), m.end(),
                  text=text, source_url=source_url, seen_spans=seen_spans)

    return out


def _add_date(
    out: list[ExtractedFactDTO],
    d: date,
    original: str,
    start: int,
    end: int,
    *,
    text: str,
    source_url: str | None,
    seen_spans: set[tuple[int, int]],
) -> None:
    if _overlaps(seen_spans, start, end):
        return
    seen_spans.add((start, end))

    date_type = _guess_date_type(text, start)
    context = _quote_around(text, start, end, window=50)
    confidence = 0.90 if date_type else 0.80

    value: dict[str, str | None] = {
        "date": d.isoformat(),
        "original": original.strip(),
    }
    if date_type:
        value["type"] = date_type

    out.append(
        ExtractedFactDTO(
            entity="document",
            field="date",
            value=value,
            verification_status=VerificationStatus.inferred,
            confidence=confidence,
            quote=context,
            source_url=source_url,
            extraction_method="regex:date",
        )
    )


def _guess_date_type(text: str, date_pos: int) -> str | None:
    """Look at text before the date for date-type hints."""
    window = text[max(0, date_pos - 150) : date_pos]
    for pattern, dtype in _DATE_TYPE_PATTERNS:
        if pattern.search(window):
            return dtype
    return None


from court_monitor.extraction._utils import quote_around as _quote_around  # noqa: E402


def _overlaps(spans: set[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < e and end > s for s, e in spans)


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

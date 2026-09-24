"""Conservative age of a reported case, from the event quotation rather than ingestion time."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum


class CaseAge(StrEnum):
    NEW = "new"
    UPDATE = "update"
    UNKNOWN = "unknown"


AGE_LABELS = {
    CaseAge.NEW: "Новое дело",
    CaseAge.UPDATE: "Обновление старого дела",
    CaseAge.UNKNOWN: "Давность неизвестна",
}


@dataclass(frozen=True)
class CaseTiming:
    age: CaseAge
    reason: str
    evidence: str = ""


_MONTHS = (
    "январ",
    "феврал",
    "март",
    "апрел",
    "ма[йея]",
    "июн",
    "июл",
    "август",
    "сентябр",
    "октябр",
    "ноябр",
    "декабр",
)
_MONTH_PATTERN = "(?:" + "|".join(_MONTHS) + r")[а-я]*"
_DATE = re.compile(
    rf"(?<!\w)(?:(?P<day>\d{{1,2}})\s+)?(?P<month>{_MONTH_PATTERN})"
    r"\s+(?P<year>20\d{2}|19\d{2})(?:\s*г(?:од[ауе]?)?\.?)?(?!\d)",
    re.IGNORECASE,
)
_NUMERIC_DATE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.((?:19|20)\d{2})(?!\d)")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*(?:году|года|год|г\.)(?![а-я])")
_RELATIVE = re.compile(r"\b(?:позавчера|вчера|сегодня)\b")
_OPENING = re.compile(r"\b(?:возбудили|возбудил[ао]?|возбужден[аоы]?|завели|завел[ао]?)\b")
_CASE = re.compile(r"\bдел[аоуе]?\b")
_UNCERTAIN = re.compile(
    r"\bне\s+(?:\w+\s+){0,2}?(?:возбу|завел|завод|завед)\w*|"
    r"\b(?:может|могут|мог|могли|могла|возможно|если|угрож\w*|гроз\w*|"
    r"планир\w*|намерен\w*|потреб\w*|попрос\w*|призва\w*|отказ\w*|"
    r"добива\w*|добить\w*|просит|просят|обещ\w*|предстоит|будет|будут)\b"
)
_HISTORICAL = re.compile(
    r"\b(?:ранее|раньше|прежде|давно|когда-то|напомним|напомнил\w*|"
    r"прошл\w+\s+(?:год\w*|месяц\w*|недел\w*)|несколько\s+лет|"
    r"\d+\s+(?:лет|год\w*|месяц\w*)\s+назад)\b"
)
_BIRTH = re.compile(r"\b(?:родил\w*|рождени\w*)\b")


def _date_ranges(text: str, published: date) -> list[tuple[date, date, str]]:
    ranges: list[tuple[date, date, str]] = []
    consumed: list[tuple[int, int]] = []
    for match in _DATE.finditer(text):
        month = next(i for i, pattern in enumerate(_MONTHS, 1) if re.match(pattern, match["month"]))
        year = int(match["year"])
        day_number = int(match["day"]) if match["day"] else None
        try:
            start = date(year, month, day_number or 1)
            end = date(year, month, day_number or calendar.monthrange(year, month)[1])
        except ValueError:
            return []
        ranges.append((start, end, match[0]))
        consumed.append(match.span())
    for match in _NUMERIC_DATE.finditer(text):
        try:
            day = date(int(match[3]), int(match[2]), int(match[1]))
        except ValueError:
            return []
        ranges.append((day, day, match[0]))
        consumed.append(match.span())
    for match in _YEAR.finditer(text):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        year = int(match[1])
        ranges.append((date(year, 1, 1), date(year, 12, 31), match[0]))
    for match in _RELATIVE.finditer(text):
        offset = {"сегодня": 0, "вчера": 1, "позавчера": 2}[match[0]]
        day = published - timedelta(days=offset)
        ranges.append((day, day, match[0]))
    return ranges


def case_timing(
    quotation: str, *, published: date, period_start: date, period_end: date
) -> CaseTiming:
    """Classify only explicit time evidence; do not equate a person with a single case.

    Multiple time references, birth dates and uncertain statements need review. A
    sentence reporting a recent verdict alone does not establish when its case began.
    """
    text = quotation.lower().replace("ё", "е")
    unknown = CaseTiming(CaseAge.UNKNOWN, "В цитате недостаточно сведений о начале дела")
    if _UNCERTAIN.search(text) or _BIRTH.search(text):
        return CaseTiming(CaseAge.UNKNOWN, "Фактичность или принадлежность даты требует проверки")
    ranges = _date_ranges(text, published)
    if len(ranges) != 1:
        return unknown
    start, end, evidence = ranges[0]
    if end < period_start:
        return CaseTiming(
            CaseAge.UPDATE, "В цитате указано событие до выбранного периода", evidence
        )
    if (
        _OPENING.search(text)
        and _CASE.search(text)
        and not _HISTORICAL.search(text)
        and period_start <= start <= end <= min(period_end, published)
    ):
        return CaseTiming(
            CaseAge.NEW, "В цитате сообщается о возбуждении дела в выбранном периоде", evidence
        )
    return unknown

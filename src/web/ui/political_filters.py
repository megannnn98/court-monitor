"""The filters of «Результат»: what the operator chose, and how the choice is kept.

A period of the latest news — the last months, or dates —, what the latest news is, what
the operator's base says, whether the people marked «обработано» are shown, and whether
the people with a name or without one. The page, its Excel file and the cookie that
remembers the choice read the same `Filters`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import parse_qs, unquote

from entities.known_base import LEVEL_LABELS
from entities.news import CLOSED, NEW_CASE, ONGOING, OTHER, SENTENCE, UNKNOWN

PERIODS = {0: "За всё время", 1: "Месяц", 3: "3 месяца", 6: "Полгода", 12: "Год"}
# The last filters, so that a reload or the menu's link keeps the period.
FILTERS_COOKIE = "political_filters"
FILTER_NAMES = ("months", "date_from", "date_to", "news", "known", "done", "who")
# Named people, or the figurants a publication does not name (`entities.unnamed_cases`).
WHO_FILTERS = {"all": "Все", "named": "С именем", "unnamed": "Без имени"}
# The people the operator marked «обработано»: hidden unless she asks to see them.
DONE_FILTERS = ("hide", "show")
# What the latest news is (`entities.news`): the operator's new cases and sentences first.
NEWS_FILTERS = {
    "all": "Любая свежая новость",
    NEW_CASE: "Новые дела",
    SENTENCE: "Приговоры",
    ONGOING: "Продолжение дела",
    CLOSED: "Дело завершено",
    OTHER: "Другое",
    UNKNOWN: "Не определено",
}

# Whether the operator's own base («Найденные люди») already holds the person, by name
# (`entities.known_base`): the new people are the ones it does not.
KNOWN_FILTERS = {
    "all": "Любые",
    "none": "Нет в базе",
    "in_base": LEVEL_LABELS["in_base"].capitalize(),
    "probably": LEVEL_LABELS["probably"].capitalize(),
    "namesakes": LEVEL_LABELS["namesakes"].capitalize(),
    "similar": LEVEL_LABELS["similar"].capitalize(),
}


@dataclass(frozen=True)
class Filters:
    """The list's filters: a period of the latest news — the last months, or dates — and
    what the latest news is."""

    months: int = 0
    date_from: date | None = None
    date_to: date | None = None
    news: str = "all"
    known: str = "all"
    done: str = "hide"
    who: str = "all"

    @property
    def custom(self) -> bool:
        return self.date_from is not None or self.date_to is not None

    def query(self) -> dict[str, str]:
        """As URL parameters, for the pager and the Excel link."""
        return {
            "months": str(self.months),
            "date_from": self.date_from.isoformat() if self.date_from else "",
            "date_to": self.date_to.isoformat() if self.date_to else "",
            "news": self.news,
            "known": self.known,
            "done": self.done,
            "who": self.who,
        }

    def window(self, now: datetime) -> tuple[datetime | None, datetime | None]:
        """The period as moments: from the first (inclusive) up to the second (exclusive).
        The dates, when given, are whole days; else the last months back from `now`."""
        if self.months:
            return now - timedelta(days=30 * self.months), None
        start = datetime.combine(self.date_from, time.min, UTC) if self.date_from else None
        # The whole last day.
        end = (
            datetime.combine(self.date_to + timedelta(days=1), time.min, UTC)
            if self.date_to
            else None
        )
        return start, end


def _parse_date(text: str) -> date | None:
    """«25/09/2026» as the form writes it (day/month/year), «25.09.2026», or the
    ISO «2026-09-25» of the links and the cookie."""
    value = text.strip()
    if not value:
        return None
    for layout in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, layout).date()  # noqa: DTZ007 - a date, no time
        except ValueError:
            continue
    return None


_CALENDAR_ICON = (
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>'
)


def _form_date(value: date | None) -> str:
    return f"{value:%d/%m/%Y}" if value else ""


def date_field(name: str, label: str, value: date | None) -> str:
    """A day/month/year field, whatever the browser's language: a native date field
    shows the browser's own order (month first in an English one)."""
    return (
        f'<label class="dates">{label} <span class="date-pick"><input type="text" name="{name}" '
        f'value="{_form_date(value)}" placeholder="дд/мм/гггг" inputmode="numeric" '
        'pattern="\\d{1,2}/\\d{1,2}/\\d{4}" title="день/месяц/год" size="10" '
        "data-date>"
        # The browser's calendar, unnamed and unseen: it only fills the field above.
        '<button type="button" class="secondary date-open" title="Выбрать в календаре" '
        f'aria-label="Календарь">{_CALENDAR_ICON}</button>'
        '<input type="date" class="date-native" tabindex="-1" aria-hidden="true" '
        f'value="{value.isoformat() if value else ""}"></span></label>'
    )


def filters(
    months: int,
    date_from: str,
    date_to: str,
    news: str = "all",
    known: str = "all",
    done: str = "hide",
    who: str = "all",
) -> Filters:
    """Dates, when given, win over the months."""
    start, end = _parse_date(date_from), _parse_date(date_to)
    return Filters(
        months=0 if start or end or months not in PERIODS else months,
        date_from=start,
        date_to=end,
        news=news if news in NEWS_FILTERS else "all",
        known=known if known in KNOWN_FILTERS else "all",
        done=done if done in DONE_FILTERS else "hide",
        who=who if who in WHO_FILTERS else "all",
    )


def remembered(cookie: str) -> Filters | None:
    """The filters the cookie keeps; None when it keeps nothing usable."""
    # The page's script writes it encoded; the server, plain.
    values = {name: items[-1] for name, items in parse_qs(unquote(cookie)).items()}
    if not values:
        return None
    try:
        months = int(values.get("months") or 0)
    except ValueError:
        months = 0
    return filters(
        months,
        values.get("date_from", "")[:10],
        values.get("date_to", "")[:10],
        values.get("news", "all"),
        values.get("known", "all"),
        values.get("done", "hide"),
        values.get("who", "all"),
    )

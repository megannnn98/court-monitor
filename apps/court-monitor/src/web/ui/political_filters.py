"""The filters of «Результат»: what the operator chose, and how the choice is kept.

A period of the latest news — the last months, or dates —, what the latest news is, what
the operator's base says, whether the people marked «обработано» are shown, and whether
the people with a name or without one. The page, its Excel file and the cookie that
remembers the choice read the same `Filters`.
"""

from __future__ import annotations

from datetime import date

from web.views.political_filters import _form_date

_CALENDAR_ICON = (
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>'
)


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

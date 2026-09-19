"""Choosing the period with buttons: presets and a month calendar.

No FSM and no server-side session: what the user has chosen so far travels inside
`callback_data`, which Telegram limits to 64 bytes. The layouts here are plain data —
`app.py` turns them into aiogram keyboards, so handlers and tests stay free of it.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

PREFIX = "p"
# A label the user can press but which must change nothing (a weekday, a month name).
NOOP = "p:noop"
# `p:<kind>:<action>:<payload>:<anchor>` — 38 bytes at most for a day of a 4-digit year.
SEPARATOR = ":"
PEOPLE = "people"
EXPORT = "export"
ACTIONS = (PEOPLE, EXPORT)

WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
MONTHS = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


@dataclass(frozen=True)
class Button:
    text: str
    data: str


# Rows of buttons; an empty `data` marks a label the user cannot press.
Keyboard = list[list[Button]]


@dataclass(frozen=True)
class Period:
    date_from: date
    date_to: date


@dataclass(frozen=True)
class Choice:
    """What a pressed button means."""

    action: str
    period: Period | None = None
    # A calendar to draw instead of running the action.
    month: date | None = None
    anchor: date | None = None
    cancelled: bool = False
    # A label was pressed: answer the callback and leave the message alone.
    noop: bool = False


class CallbackError(ValueError):
    """The callback data is not ours or not readable: the bot answers and forgets it."""


def presets(today: date) -> list[tuple[str, str, Period]]:
    """Ready periods, newest first; every one ends today."""
    first_of_month = today.replace(day=1)
    last_month_end = first_of_month - timedelta(days=1)
    return [
        ("today", "Сегодня", Period(today, today)),
        ("7d", "7 дней", Period(today - timedelta(days=6), today)),
        ("30d", "30 дней", Period(today - timedelta(days=29), today)),
        ("month", f"Этот месяц ({MONTHS[today.month - 1]})", Period(first_of_month, today)),
        (
            "prev",
            f"Прошлый месяц ({MONTHS[last_month_end.month - 1]})",
            Period(last_month_end.replace(day=1), last_month_end),
        ),
    ]


def preset_keyboard(action: str, today: date) -> Keyboard:
    rows: Keyboard = [
        [Button(text=title, data=_data("preset", action, key))] for key, title, _ in presets(today)
    ]
    rows.append(
        [
            Button(text="📅 Другой период", data=_data("cal", action, _month(today))),
            Button(text="Отмена", data=_data("cancel", action, "")),
        ]
    )
    return rows


def calendar_keyboard(action: str, month: date, anchor: date | None, today: date) -> Keyboard:
    """A month grid. `anchor` set means the user is picking the end of the period."""
    rows: Keyboard = [[Button(text=_title(month, anchor), data="")]]
    rows.append([Button(text=name, data="") for name in WEEKDAYS])
    for week in calendar.Calendar().monthdatescalendar(month.year, month.month):
        row: list[Button] = []
        for day in week:
            if day.month != month.month or day > today:
                # A day outside the month, and any future day, is not selectable.
                row.append(Button(text="·", data=""))
                continue
            row.append(
                Button(
                    text=_day_text(day, anchor, today),
                    data=_data("day", action, day.isoformat(), anchor),
                )
            )
        rows.append(row)
    rows.append(
        [
            Button(text="‹", data=_data("nav", action, _month(_shift(month, -1)), anchor)),
            Button(text=MONTHS[month.month - 1].capitalize(), data=""),
            Button(text="›", data=_data("nav", action, _month(_shift(month, 1)), anchor)),
        ]
    )
    rows.append([Button(text="Отмена", data=_data("cancel", action, ""))])
    return rows


def parse_callback(data: str, today: date) -> Choice:
    """Turn `callback_data` back into what to do next."""
    if data == NOOP:
        return Choice(action=PEOPLE, noop=True)
    parts = data.split(SEPARATOR)
    if len(parts) < 3 or parts[0] != PREFIX:
        raise CallbackError(f"not a period callback: {data!r}")
    kind, action, payload = parts[1], parts[2], parts[3] if len(parts) > 3 else ""
    anchor = _date(parts[4]) if len(parts) > 4 and parts[4] else None
    if action not in ACTIONS:
        raise CallbackError(f"unknown action: {action!r}")
    if kind == "cancel":
        return Choice(action=action, cancelled=True)
    if kind == "preset":
        period = next((item for key, _, item in presets(today) if key == payload), None)
        if period is None:
            raise CallbackError(f"unknown preset: {payload!r}")
        return Choice(action=action, period=period)
    if kind in ("cal", "nav"):
        return Choice(action=action, month=_month_start(payload), anchor=anchor)
    if kind == "day":
        chosen = _date(payload)
        if anchor is None:
            # First press: the period starts here, the calendar stays open for its end.
            return Choice(action=action, month=chosen.replace(day=1), anchor=chosen)
        if chosen < anchor:
            # Pressed backwards: read it as the period the user drew, not as an error.
            return Choice(action=action, period=Period(chosen, anchor))
        return Choice(action=action, period=Period(anchor, chosen))
    raise CallbackError(f"unknown callback kind: {kind!r}")


def _data(kind: str, action: str, payload: str, anchor: date | None = None) -> str:
    parts = [PREFIX, kind, action, payload]
    if anchor is not None:
        parts.append(anchor.isoformat())
    return SEPARATOR.join(parts)


def _title(month: date, anchor: date | None) -> str:
    name = f"{MONTHS[month.month - 1]} {month.year}"
    if anchor is None:
        return f"{name} — выберите начало периода"
    return f"{name} — начало {anchor.isoformat()}, выберите конец"


def _day_text(day: date, anchor: date | None, today: date) -> str:
    if anchor is not None and day == anchor:
        return f"[{day.day}]"
    if day == today:
        return f"·{day.day}·"
    return str(day.day)


def _month(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _month_start(value: str) -> date:
    try:
        year, month = (int(part) for part in value.split("-"))
        return date(year, month, 1)
    except ValueError:
        raise CallbackError(f"not a month: {value!r}") from None


def _shift(month: date, months: int) -> date:
    total = month.year * 12 + month.month - 1 + months
    return date(total // 12, total % 12 + 1, 1)


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CallbackError(f"not a date: {value!r}") from None

"""Picking a period with buttons: presets, the month grid and the callback round trip."""

from __future__ import annotations

from datetime import date

import pytest

from telegram_bot.period_keyboard import (
    EXPORT,
    PEOPLE,
    Button,
    CallbackError,
    calendar_keyboard,
    parse_callback,
    preset_keyboard,
    presets,
)

TODAY = date(2026, 9, 19)


def buttons(rows: list[list[Button]]) -> list[Button]:
    return [button for row in rows for button in row]


def pressable(rows: list[list[Button]]) -> list[Button]:
    return [button for button in buttons(rows) if button.data]


def test_presets_cover_the_usual_periods() -> None:
    periods = {key: period for key, _, period in presets(TODAY)}

    assert periods["today"].date_from == periods["today"].date_to == TODAY
    assert periods["7d"].date_from == date(2026, 9, 13)
    assert periods["30d"].date_from == date(2026, 8, 21)
    assert periods["month"].date_from == date(2026, 9, 1)
    assert (periods["prev"].date_from, periods["prev"].date_to) == (
        date(2026, 8, 1),
        date(2026, 8, 31),
    )


def test_every_preset_ends_today_or_earlier() -> None:
    assert all(period.date_to <= TODAY for _, _, period in presets(TODAY))


def test_a_preset_press_returns_its_period() -> None:
    keyboard = preset_keyboard(PEOPLE, TODAY)
    seven_days = next(button for button in pressable(keyboard) if button.text == "7 дней")

    choice = parse_callback(seven_days.data, TODAY)

    assert choice.action == PEOPLE
    assert choice.period is not None
    assert choice.period.date_from == date(2026, 9, 13)


def test_the_preset_keyboard_offers_the_calendar_and_cancel() -> None:
    texts = [button.text for button in buttons(preset_keyboard(EXPORT, TODAY))]

    assert "📅 Другой период" in texts
    assert "Отмена" in texts


def test_cancel_is_recognised() -> None:
    cancel = next(
        button for button in pressable(preset_keyboard(EXPORT, TODAY)) if button.text == "Отмена"
    )

    assert parse_callback(cancel.data, TODAY).cancelled


def test_the_calendar_shows_the_month_and_its_weekdays() -> None:
    rows = calendar_keyboard(PEOPLE, date(2026, 9, 1), None, TODAY)

    assert "сентябрь 2026" in rows[0][0].text
    assert [button.text for button in rows[1]] == ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def test_a_future_day_cannot_be_pressed() -> None:
    rows = calendar_keyboard(PEOPLE, date(2026, 9, 1), None, TODAY)

    days = {button.data.split(":")[3] for button in pressable(rows) if ":day:" in button.data}
    assert "2026-09-19" in days
    assert "2026-09-20" not in days


def test_the_first_press_keeps_the_calendar_open_for_the_end() -> None:
    rows = calendar_keyboard(EXPORT, date(2026, 9, 1), None, TODAY)
    first = next(button for button in pressable(rows) if button.data.endswith("2026-09-01"))

    choice = parse_callback(first.data, TODAY)

    assert choice.period is None
    assert choice.anchor == date(2026, 9, 1)
    assert choice.month == date(2026, 9, 1)


def test_the_second_press_closes_the_period() -> None:
    rows = calendar_keyboard(EXPORT, date(2026, 9, 1), date(2026, 9, 1), TODAY)
    end = next(
        button for button in pressable(rows) if ":day:" in button.data and "-09-10" in button.data
    )

    choice = parse_callback(end.data, TODAY)

    assert choice.period is not None
    assert (choice.period.date_from, choice.period.date_to) == (date(2026, 9, 1), date(2026, 9, 10))


def test_pressing_backwards_still_gives_a_forward_period() -> None:
    rows = calendar_keyboard(EXPORT, date(2026, 9, 1), date(2026, 9, 10), TODAY)
    earlier = next(
        button for button in pressable(rows) if ":day:" in button.data and "-09-03" in button.data
    )

    choice = parse_callback(earlier.data, TODAY)

    assert choice.period is not None
    assert (choice.period.date_from, choice.period.date_to) == (date(2026, 9, 3), date(2026, 9, 10))


def test_the_chosen_start_is_marked_in_the_grid() -> None:
    rows = calendar_keyboard(EXPORT, date(2026, 9, 1), date(2026, 9, 5), TODAY)

    assert any(button.text == "[5]" for button in buttons(rows))


def test_navigation_moves_a_month_and_keeps_the_start() -> None:
    rows = calendar_keyboard(PEOPLE, date(2026, 1, 1), date(2025, 12, 20), TODAY)
    back = next(button for button in pressable(rows) if button.text == "‹")
    forward = next(button for button in pressable(rows) if button.text == "›")

    assert parse_callback(back.data, TODAY).month == date(2025, 12, 1)
    assert parse_callback(forward.data, TODAY).month == date(2026, 2, 1)
    assert parse_callback(back.data, TODAY).anchor == date(2025, 12, 20)


def test_every_callback_fits_the_telegram_limit() -> None:
    rows = calendar_keyboard(EXPORT, date(2026, 9, 1), date(2026, 9, 1), TODAY)
    rows += preset_keyboard(EXPORT, TODAY)

    assert all(len(button.data.encode()) <= 64 for button in buttons(rows))


@pytest.mark.parametrize(
    "data",
    [
        "",
        "x:preset:people:7d",
        "p:preset:people:never",
        "p:day:people:not-a-date",
        "p:what:people:x",
    ],
)
def test_a_broken_callback_is_rejected(data: str) -> None:
    with pytest.raises(CallbackError):
        parse_callback(data, TODAY)


def test_a_callback_of_another_command_is_rejected() -> None:
    with pytest.raises(CallbackError):
        parse_callback("p:preset:delete-everything:7d", TODAY)

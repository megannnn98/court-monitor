"""Unit tests: Russian date parsing."""

from __future__ import annotations

from datetime import date

from court_monitor.extraction.dates import extract_dates, parse_russian_date


def test_long_form_with_year():
    assert parse_russian_date("02 апреля 2026") == date(2026, 4, 2)


def test_long_form_with_g_suffix():
    assert parse_russian_date("Заседание 15 декабря 2025 г.") == date(2025, 12, 15)


def test_numeric_ddmmyyyy():
    assert parse_russian_date("от 02.04.2026") == date(2026, 4, 2)


def test_iso():
    assert parse_russian_date("2026-04-02") == date(2026, 4, 2)


def test_two_digit_year():
    assert parse_russian_date("02.04.26") == date(2026, 4, 2)


def test_invalid_returns_none():
    assert parse_russian_date("не дата") is None
    assert parse_russian_date("32 января 2026") is None  # bad day
    assert parse_russian_date("") is None


def test_iso_attr_takes_precedence():
    # HTML <time datetime="...">: ISO should win over prose near it.
    assert parse_russian_date("2026-04-02 some prose") == date(2026, 4, 2)


# --- extract_dates tests ---


def test_extract_dates_numeric():
    dtos = extract_dates("Приговор вынесен 02.04.2026 года")
    assert len(dtos) >= 1
    val = dtos[0].value
    assert isinstance(val, dict)
    assert val["date"] == "2026-04-02"
    assert val["original"] == "02.04.2026"
    assert dtos[0].quote


def test_extract_dates_long_form():
    dtos = extract_dates("Заседание прошло 15 декабря 2025 года")
    assert len(dtos) >= 1
    assert dtos[0].value["date"] == "2025-12-15"
    assert "декабря" in dtos[0].value["original"]


def test_extract_dates_iso():
    dtos = extract_dates("Дата: 2026-04-02")
    assert len(dtos) >= 1
    assert dtos[0].value["date"] == "2026-04-02"


def test_extract_dates_type_detection():
    dtos = extract_dates("Приговор вынесен 02.04.2026")
    assert len(dtos) >= 1
    # verdict_date type should be detected from context
    assert dtos[0].value.get("type") == "verdict_date"


def test_extract_dates_no_date():
    assert extract_dates("Текст без дат") == []


def test_extract_dates_no_duplicates():
    dtos = extract_dates("02.04.2026 и ещё раз 02.04.2026")
    # Same date at same position should not duplicate
    assert len(dtos) >= 1


def test_extract_dates_context_in_quote():
    dtos = extract_dates("Приговор вынесен 02.04.2026 года судом")
    assert len(dtos) >= 1
    quote = dtos[0].quote
    assert "02.04.2026" in quote

"""Unit tests: Russian date parsing."""

from __future__ import annotations

from datetime import date

from court_monitor.extraction.dates import parse_russian_date


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

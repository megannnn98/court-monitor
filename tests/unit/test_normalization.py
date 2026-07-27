"""Unit tests: FIO / text normalization (stage-1 subset)."""

from __future__ import annotations

from court_monitor.normalization import normalize_fio, normalize_fio_parts
from court_monitor.sources.base import normalize_text


def test_normalize_whitespace():
    assert normalize_text("  a\n b \tc") == "a b c"


def test_fio_yo_to_e_and_casefold():
    assert normalize_fio("Алёна Ёлкина") == "алена елкина"


def test_fio_dash_and_apostrophe():
    assert normalize_fio("Иван-Дальний") == "иван дальний"
    assert normalize_fio("O'Brien") == "o brien"


def test_fio_parts():
    assert normalize_fio_parts("Иванов Иван Иванович") == ["иванов", "иван", "иванович"]

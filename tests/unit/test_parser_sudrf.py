"""Unit tests: sudrf press-release parser on synthetic fixtures."""

from __future__ import annotations

from datetime import date

from court_monitor.parsers.sudrf_press import parse_press_release

_FIX = """<!DOCTYPE html><html><body><article>
<h1 class="press-title">Заголовок релиза</h1>
<time class="press-date" datetime="2026-04-02">02 апреля 2026</time>
<div class="press-body"><p>Текст о ст. 205.1 УК РФ.</p></div>
</article></body></html>"""


def test_parses_title_date_text():
    parsed = parse_press_release(_FIX)
    assert parsed.title == "Заголовок релиза"
    assert parsed.published_at == date(2026, 4, 2)
    assert "205.1" in parsed.text


def test_iso_datetime_attr_preferred():
    parsed = parse_press_release(_FIX)
    assert parsed.published_at == date(2026, 4, 2)


def test_fallback_when_no_classes():
    html = "<html><body><h1>Только заголовок</h1><p>статья 282 УК РФ</p></body></html>"
    parsed = parse_press_release(html)
    assert parsed.title == "Только заголовок"
    assert "282" in parsed.text

"""Tests: Airtable shared-view parsing (rendered fixture) and CSV import."""

from __future__ import annotations

from pathlib import Path

import pytest

from court_monitor.sources.airtable_registry import (
    parse_airtable_shared_view,
    parse_registry_csv,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "airtable"


@pytest.fixture()
def rendered_view_html() -> str:
    return (FIXTURES / "source_registry.html").read_text(encoding="utf-8")


def test_parses_all_rows_from_rendered_shared_view(rendered_view_html: str) -> None:
    rows = parse_airtable_shared_view(rendered_view_html)
    assert len(rows) == 13
    names = [r.name for r in rows]
    assert "Экстремизму - НЕТ!" in names
    assert "Медиазона" in names
    assert "Кавказский узел" in names


def test_extracts_username_url_and_topics(rendered_view_html: str) -> None:
    rows = parse_airtable_shared_view(rendered_view_html)
    by_name = {r.name: r for r in rows}
    mediazona = by_name["Медиазона"]
    assert mediazona.username == "mediazzzona"
    assert mediazona.url == "https://t.me/mediazzzona"
    assert mediazona.topics == ["Новости"]

    chat = by_name["Чат про письма и книги политзаключенным"]
    assert chat.topics == ["Активисты", "Инициатива"]


def test_csv_import_matches_rendered_view() -> None:
    csv_rows = parse_registry_csv(FIXTURES / "source_registry.csv")
    assert len(csv_rows) == 13
    by_name = {r.name: r for r in csv_rows}
    assert by_name["ASTRA"].url == "https://t.me/astrapress"
    assert by_name["SOTAvision"].topics == ["Новости", "Активисты"]


def test_csv_import_handles_extra_whitespace_and_quote() -> None:
    # A tiny synthetic CSV mirroring an Airtable export, including a multi-select
    # cell with a comma inside (quoted) and a blank row.
    sample = (
        "Название,Юзернейм,Ссылка,Тип/тематика\r\n"
        '"Тест, с запятой",@test,https://t.me/test,"Новости, Активисты"\r\n'
        ",,,\r\n"
    )
    tmp = FIXTURES.parent / "registry" / "_tmp_sample.csv"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(sample, encoding="utf-8")
    try:
        rows = parse_registry_csv(tmp)
        assert len(rows) == 1
        r = rows[0]
        assert r.name == "Тест, с запятой"
        assert r.username == "test"
        assert r.topics == ["Новости", "Активисты"]
    finally:
        tmp.unlink()

"""Unit tests: parsing the live fedsfm.ru HTML list.

Runs entirely off a trimmed local fixture — no network. The fixture keeps one
example of every entry shape observed on the real page (23k entries): plain
persons, a name carrying the registry's footnote asterisk, a four-token
"ОГЛЫ" name, the "ГОДА РОЖДЕНИЯ" spelling, a person with no birthplace,
organizations with and without ИНН/ОГРН, and a multi-person ОБЪЕДИНЕНИЕ.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from court_monitor.sources.fedsfm_live import (
    FedsfmFetchError,
    fetch_live_html,
    parse_terrorists_html,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rfm" / "terrorists_page.html"


@pytest.fixture()
def result():
    return parse_terrorists_html(FIXTURE.read_text(encoding="utf-8"))


def _by_name(result, fragment: str):
    return next((r for r in result.rows if fragment in r.raw_name), None)


def test_counts_numbered_entries_only(result):
    """Nav <li>s and the unnumbered line must not inflate the total."""
    assert result.total_records == 11
    assert result.format_detected == "html"
    assert result.errors == []


def test_extracts_plain_person(result):
    row = _by_name(result, "АБАБАКАРОВ")
    assert row is not None
    assert row.raw_name == "АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ"
    assert row.birth_date == "1996-06-08"
    assert row.birth_place == "П. МАМЕДКАЛА ДЕРБЕНТСКОГО РАЙОНА РЕСПУБЛИКИ ДАГЕСТАН"
    assert row.source_ref == "5"


def test_footnote_asterisk_is_not_part_of_the_name(result):
    row = _by_name(result, "ИВАНОВ")
    assert row is not None
    assert row.raw_name == "ИВАНОВ ИВАН АЛЕКСАНДРОВИЧ"
    assert "*" not in row.normalized_name


def test_four_token_ogly_name(result):
    row = _by_name(result, "АББАСЛЫ")
    assert row is not None
    assert row.raw_name == "АББАСЛЫ ЭМИЛЬ ЭЛЬШАН ОГЛЫ"
    assert row.birth_date == "1999-12-11"


def test_goda_rozhdeniya_spelling(result):
    row = _by_name(result, "АБДУВАЛИЕВА")
    assert row is not None
    assert row.birth_date == "1976-04-21"
    assert row.birth_place == "Г. НАМАНГАН РЕСПУБЛИКИ УЗБЕКИСТАН"


def test_missing_birthplace_becomes_none(result):
    row = _by_name(result, "ЛОПУХОВ")
    assert row is not None
    assert row.birth_date == "1978-04-29"
    assert row.birth_place is None


def test_organizations_are_not_imported_as_persons(result):
    names = [r.raw_name for r in result.rows]
    for fragment in ("FREE RUSSIA", "MEMORIAL", "ПАНСИОНАТ", "СВИДЕТЕЛЕЙ ИЕГОВЫ"):
        assert not any(fragment in n for n in names), fragment


def test_multi_person_objedinenie_is_not_imported(result):
    """One record naming several people cannot become one PersonRecord."""
    assert not any("АХМЕТОВ" in r.raw_name for r in result.rows)
    assert not any("ОБЪЕДИНЕНИЕ" in r.raw_name for r in result.rows)


def test_skipped_entries_are_counted_not_dropped(result):
    """A layout change must surface as a jump in unrecognized, not silence."""
    assert result.recognized == len(result.rows)
    assert result.recognized + result.unrecognized == result.total_records
    assert result.unrecognized > 0


def test_normalized_and_search_names_are_populated(result):
    row = _by_name(result, "АБАБАКАРОВ")
    assert row.normalized_name == "абабакаров абдулла гасанович"
    assert row.search_name == "абабакаров абдулла гасанович"
    assert row.normalization_method == "rfm-html"
    assert row.normalization_confidence >= 0.9


def test_dedup_keys_are_unique_for_distinct_people(result):
    keys = [r.dedup_key for r in result.rows]
    assert len(keys) == len(set(keys))


def test_empty_html_reports_an_error():
    res = parse_terrorists_html("")
    assert res.rows == []
    assert res.errors


def test_html_without_entries_reports_a_layout_error():
    res = parse_terrorists_html("<html><body><p>ничего</p></body></html>")
    assert res.rows == []
    assert any("layout" in e for e in res.errors)


def test_fetch_raises_when_ca_bundle_is_absent(tmp_path):
    """Without the pinned chain the fetch must fail loudly, never fall back to
    an unverified connection."""
    with pytest.raises(FedsfmFetchError, match="CA bundle not found"):
        fetch_live_html(ca_bundle=tmp_path / "missing.pem")

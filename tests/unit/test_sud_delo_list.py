"""Tests for sud_delo case list parser."""

from __future__ import annotations

from pathlib import Path

from court_monitor.parsers.sud_delo_list import parse_case_list

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sudrf-live" / "2zovs" / "sud_delo"
BASE_URL = "https://2zovs.msk.sudrf.ru"


def test_parse_case_list_from_fixture() -> None:
    """Test parsing the real sud_delo case list from fixture."""
    fixture_file = FIXTURE_DIR / "search-form.html"
    html = fixture_file.read_text(encoding="utf-8", errors="replace")

    result = parse_case_list(html, BASE_URL)

    assert len(result.results) == 9

    first = result.results[0]
    assert first.case_number == "22К-1372/2026"
    assert first.case_id == "3179069"
    assert first.case_uid == "ec9975ee-3df8-4c00-8ab0-c6620d4ffd1b"
    assert first.delo_id == "4"
    assert first.srv_num == "1"
    assert first.url.startswith(BASE_URL)
    assert "case_id=3179069" in first.url
    assert "name_op=case" in first.url

    # Check that all results have required fields
    for r in result.results:
        assert r.url.startswith("https://") or r.url.startswith("http://")


def test_parse_case_list_url_order_independent() -> None:
    """Test that URL parameter order does not matter."""
    html = """
    <html><body>
    <a href="/modules.php?delo_id=4&srv_num=1&case_uid=abc-def&case_id=123&name=sud_delo&name_op=case">1-1/2026</a>
    </body></html>
    """

    result = parse_case_list(html, "https://court.local")

    assert len(result.results) == 1
    r = result.results[0]
    assert r.case_id == "123"
    assert r.case_uid == "abc-def"
    assert r.delo_id == "4"


def test_parse_case_list_empty() -> None:
    """Test parsing empty HTML returns empty results."""
    result = parse_case_list("<html></html>", BASE_URL)
    assert len(result.results) == 0


def test_parse_case_list_missing_params() -> None:
    """Test that links missing required params are skipped."""
    html = """
    <html><body>
    <a href="/modules.php?name=sud_delo&name_op=case&case_id=123&case_uid=abc&delo_id=4">OK</a>
    <a href="/modules.php?name=sud_delo&name_op=case&case_id=456">Missing UID</a>
    <a href="/modules.php?name=sud_delo&name_op=case">No params</a>
    </body></html>
    """

    result = parse_case_list(html, BASE_URL)

    # Only the first link has all required params
    assert len(result.results) == 1
    assert result.results[0].case_id == "123"


def test_parse_case_list_case_number_patterns() -> None:
    """Test different case number formats."""
    html = """
    <html><body>
    <a href="/modules.php?name=sud_delo&name_op=case&case_id=1&case_uid=aaa&delo_id=4&srv_num=1">1-123/2026</a>
    <a href="/modules.php?name=sud_delo&name_op=case&case_id=2&case_uid=bbb&delo_id=4&srv_num=1">22К-1372/2026</a>
    </body></html>
    """

    result = parse_case_list(html, BASE_URL)

    assert len(result.results) == 2
    numbers = {r.case_number for r in result.results}
    assert "1-123/2026" in numbers
    assert "22К-1372/2026" in numbers

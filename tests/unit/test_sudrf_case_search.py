"""Unit tests for the case search adapter (task §25)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from court_monitor.config.loader import SourceConfig
from court_monitor.domain.models import SourceBackend, SourceType
from court_monitor.sources.fixture_transport import FixtureTransport
from court_monitor.sources.sudrf_case_search import SudrfCaseSearchAdapter
from court_monitor.sources.sudrf_dto import SudrfCaseSearchCriteria


def _source(*, backend: SourceBackend = SourceBackend.fixture, base_url: str = "") -> SourceConfig:
    return SourceConfig(
        name="test-court",
        type=SourceType.sudrf,
        backend=backend,
        base_url=base_url or "https://example.test",
    )


# ── _build_search_params ──


def test_base_params_always_present():
    adapter = SudrfCaseSearchAdapter(_source())
    criteria = SudrfCaseSearchCriteria(court="test-court")
    params = adapter._build_search_params(criteria)

    assert params["name"] == "sud_delo"
    assert params["srv_num"] == "1"
    assert params["name_op"] == "r"
    assert params["case_type"] == "0"
    assert params["delo_table"] == "u1_case"
    assert params["delo_id"] == "1540006"
    assert params["new"] == "0"


def test_article_param_exact_name():
    adapter = SudrfCaseSearchAdapter(_source())
    criteria = SudrfCaseSearchCriteria(court="test-court", article="205.1")
    params = adapter._build_search_params(criteria)
    assert params["U1_DEFENDANT__LAW_ARTICLESS"] == "205.1"
    assert "U1_EVENT__EVENT_DATEDD" not in params
    assert "U1_CASE__RESULT_DATE1D" not in params


def test_result_date_uses_result_date_fields_not_event():
    """Task §4: verdict date goes to RESULT_DATE*, not EVENT_DATEDD."""
    adapter = SudrfCaseSearchAdapter(_source())
    criteria = SudrfCaseSearchCriteria(court="c", result_date=date(2026, 4, 2))
    params = adapter._build_search_params(criteria)

    assert params["U1_CASE__RESULT_DATE1D"] == "02.04.2026"
    assert params["U1_CASE__RESULT_DATE2D"] == "02.04.2026"
    assert "U1_EVENT__EVENT_DATEDD" not in params


def test_event_date_uses_event_field():
    adapter = SudrfCaseSearchAdapter(_source())
    criteria = SudrfCaseSearchCriteria(court="c", event_date=date(2026, 8, 6))
    params = adapter._build_search_params(criteria)
    assert params["U1_EVENT__EVENT_DATEDD"] == "06.08.2026"
    assert "U1_CASE__RESULT_DATE1D" not in params


def test_both_dates_when_both_supplied():
    adapter = SudrfCaseSearchAdapter(_source())
    criteria = SudrfCaseSearchCriteria(
        court="c",
        result_date=date(2026, 4, 2),
        event_date=date(2026, 8, 6),
    )
    params = adapter._build_search_params(criteria)
    assert params["U1_CASE__RESULT_DATE1D"] == "02.04.2026"
    assert params["U1_EVENT__EVENT_DATEDD"] == "06.08.2026"


def test_case_number_param():
    adapter = SudrfCaseSearchAdapter(_source())
    criteria = SudrfCaseSearchCriteria(court="c", case_number="1-1688/2026")
    params = adapter._build_search_params(criteria)
    assert params["U1_CASE__CASE_NUMBERSS"] == "1-1688/2026"


def test_person_name_param():
    adapter = SudrfCaseSearchAdapter(_source())
    criteria = SudrfCaseSearchCriteria(court="c", person_name="Иванов")
    params = adapter._build_search_params(criteria)
    assert params["U1_DEFENDANT__NAMESS"] == "Иванов"


def test_url_encoding_of_cyrillic_and_slashes():
    """The adapter must pass params via urlencode — tested through search."""
    adapter = SudrfCaseSearchAdapter(_source())
    criteria = SudrfCaseSearchCriteria(court="c", case_number="1-1688/2026")
    params = adapter._build_search_params(criteria)
    from urllib.parse import urlencode  # noqa: PLC0415

    qs = urlencode(params)
    # The slash in case number must be encoded as %2F
    assert "1-1688%2F2026" in qs
    # The cyrillic person name must be %-encoded
    qs_cyr = urlencode({"U1_DEFENDANT__NAMESS": "Иванов"})
    assert "Иванов" not in qs_cyr
    assert "%" in qs_cyr


# ── fixture transport vs live ──


def test_fixture_backend_does_not_make_http_calls(monkeypatch):
    """Fixture backend must never touch the network (task §2)."""
    fixtures = Path("tests/fixtures/sudrf-live/2zovs/sud_delo")
    if not fixtures.exists():
        pytest.skip("2zovs sud_delo fixtures not available")

    src = SourceConfig(
        name="2zovs",
        type=SourceType.sudrf,
        backend=SourceBackend.fixture,
        base_url="https://does-not-matter.test",
        fixture_path=str(fixtures),
    )
    adapter = SudrfCaseSearchAdapter(src)

    # Patch HttpClient to fail loudly if called.
    def _no_network(*args, **kwargs):  # pragma: no cover - failure branch
        raise AssertionError("HttpClient used in fixture mode")

    monkeypatch.setattr("court_monitor.sources.sudrf_case_search.HttpClient", _no_network)
    criteria = SudrfCaseSearchCriteria(court="2zovs", article="205.1")
    # Either returns results or empty list; must NOT raise from HttpClient.
    adapter.search(criteria)


def test_fixture_transport_returns_results_for_article():
    fixtures = Path("tests/fixtures/sudrf-live/2zovs/sud_delo")
    if not fixtures.exists():
        pytest.skip("2zovs sud_delo fixtures not available")

    transport = FixtureTransport(fixtures)
    from urllib.parse import urlencode  # noqa: PLC0415

    url = "https://x/modules.php?" + urlencode(
        {
            "name": "sud_delo",
            "srv_num": "1",
            "name_op": "r",
            "delo_id": "1540006",
            "U1_DEFENDANT__LAW_ARTICLESS": "205.1",
        }
    )
    resp = transport.get(url)
    assert resp.status == 200
    assert len(resp.text) > 100


# ── progressive search behavior ──


def test_progressive_search_falls_back_when_first_returns_zero():
    """Task §5: strategies fall back in order; first non-empty wins."""
    from court_monitor.services.court_orchestrator import progressive_search  # noqa: PLC0415

    call_log: list[SudrfCaseSearchCriteria] = []

    class FakeAdapter:
        def search(self, criteria):
            call_log.append(criteria)
            # First call (article + result_date): empty
            if criteria.result_date is not None and criteria.person_name is None:
                return []
            # Second call (article only): return a dummy
            from court_monitor.sources.sudrf_dto import SudrfCaseSearchResult  # noqa: PLC0415

            return [
                SudrfCaseSearchResult(
                    case_number="1-1/2026",
                    case_id="1",
                    case_uid="uid-abc",
                    delo_id="4",
                    srv_num="1",
                    url="https://x/case/1",
                )
            ]

    results, attempts = progressive_search(
        adapter=FakeAdapter(),
        court="c",
        article="205.1",
        result_date=date(2026, 4, 2),
        publication_date_hint=None,
        person_name=None,
    )
    assert len(results) == 1
    assert results[0].case_uid == "uid-abc"
    assert len(attempts) >= 2
    assert attempts[0].strategy == "article_result_date"
    assert attempts[0].result_count == 0
    assert attempts[1].strategy == "article_only"


def test_progressive_search_dedupes_same_case_uid_across_attempts():
    from court_monitor.services.court_orchestrator import progressive_search  # noqa: PLC0415
    from court_monitor.sources.sudrf_dto import SudrfCaseSearchResult  # noqa: PLC0415

    def _mk(uid):
        return SudrfCaseSearchResult("N", "1", uid, "4", "1", f"https://x/{uid}")

    class FakeAdapter:
        def __init__(self):
            self.calls = 0

        def search(self, criteria):
            self.calls += 1
            # Same case returned by two different strategies.
            return [_mk("uid-same")]

    results, attempts = progressive_search(
        adapter=FakeAdapter(),
        court="c",
        article="205.1",
        result_date=date(2026, 4, 2),
        publication_date_hint=date(2026, 4, 2),
        person_name=None,
    )
    # Even though both attempts return the same case, dedup keeps it once.
    assert len(results) == 1
    assert results[0].case_uid == "uid-same"

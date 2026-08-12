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


# ── transport semantics (task §2, §11) ──


from court_monitor.domain.models import FetchHealth  # noqa: E402
from court_monitor.sources.http_client import HttpResponse  # noqa: E402
from court_monitor.sources.sudrf_case_search import (  # noqa: E402
    SudrfBlockedError,
    SudrfTemporaryError,
    SudrfTransportError,
)


class _FakeTransport:
    """Stand-in for HttpClient / FixtureTransport returning a canned response."""

    def __init__(self, response: HttpResponse) -> None:
        self._response = response

    def get(self, url: str) -> HttpResponse:  # noqa: ARG002
        return self._response

    def __enter__(self) -> _FakeTransport:
        return self

    def __exit__(self, *exc: object) -> None:
        pass


def _adapter_with_transport(response: HttpResponse) -> SudrfCaseSearchAdapter:
    cfg = _source()
    return SudrfCaseSearchAdapter(cfg, http_client_factory=lambda: _FakeTransport(response))


def test_search_ok_empty_body_returns_empty_list():
    """HTTP 200 + health=ok + no cases in body → legitimate empty result."""
    resp = HttpResponse(
        status=200,
        text='<table class="sud_delo"></table>',
        url="https://example.test/modules.php",
        health=FetchHealth.ok,
    )
    adapter = _adapter_with_transport(resp)
    results = adapter.search(SudrfCaseSearchCriteria(court="c", article="205.1"))
    assert results == []


def test_search_200_blocked_raises_not_empty():
    """HTTP 200 + CAPTCHA body → SudrfBlockedError, NOT []."""
    resp = HttpResponse(
        status=200,
        text="<html>captcha проверка безопасности</html>",
        url="https://example.test/modules.php",
        health=FetchHealth.blocked,
    )
    adapter = _adapter_with_transport(resp)
    with pytest.raises(SudrfBlockedError):
        adapter.search(SudrfCaseSearchCriteria(court="c", article="205.1"))


def test_search_timeout_raises_not_empty():
    """Timeout → SudrfTemporaryError, NOT []."""
    resp = HttpResponse(
        status=0, text="", url="https://example.test/modules.php", health=FetchHealth.timeout
    )
    adapter = _adapter_with_transport(resp)
    with pytest.raises(SudrfTemporaryError):
        adapter.search(SudrfCaseSearchCriteria(court="c", article="205.1"))


def test_search_http_500_raises_not_empty():
    """HTTP 500 → SudrfTransportError, NOT []."""
    resp = HttpResponse(
        status=500, text="", url="https://example.test/modules.php", health=FetchHealth.http_error
    )
    adapter = _adapter_with_transport(resp)
    with pytest.raises(SudrfTransportError):
        adapter.search(SudrfCaseSearchCriteria(court="c", article="205.1"))


def test_fetch_card_200_blocked_raises():
    """Case card: HTTP 200 + blocked → SudrfBlockedError, NOT None."""
    resp = HttpResponse(
        status=200,
        text="captcha check",
        url="https://example.test/case/1",
        health=FetchHealth.blocked,
    )
    adapter = _adapter_with_transport(resp)
    from court_monitor.sources.sudrf_dto import SudrfCaseSearchResult  # noqa: PLC0415

    result = SudrfCaseSearchResult(
        "1-1/2026", "1", "uid-1", "4", "1", "https://example.test/case/1"
    )
    with pytest.raises(SudrfBlockedError):
        adapter.fetch_case_card_html(result)


def test_fetch_card_timeout_raises():
    """Case card: timeout → SudrfTemporaryError, NOT None."""
    resp = HttpResponse(
        status=0, text="", url="https://example.test/case/1", health=FetchHealth.timeout
    )
    adapter = _adapter_with_transport(resp)
    from court_monitor.sources.sudrf_dto import SudrfCaseSearchResult  # noqa: PLC0415

    result = SudrfCaseSearchResult(
        "1-1/2026", "1", "uid-1", "4", "1", "https://example.test/case/1"
    )
    with pytest.raises(SudrfTemporaryError):
        adapter.fetch_case_card_html(result)


def test_fetch_card_ok_returns_html():
    """Case card: 200 + ok → returns HTML body."""
    resp = HttpResponse(
        status=200,
        text="<html>case card content</html>",
        url="https://example.test/case/1",
        health=FetchHealth.ok,
    )
    adapter = _adapter_with_transport(resp)
    from court_monitor.sources.sudrf_dto import SudrfCaseSearchResult  # noqa: PLC0415

    result = SudrfCaseSearchResult(
        "1-1/2026", "1", "uid-1", "4", "1", "https://example.test/case/1"
    )
    html = adapter.fetch_case_card_html(result)
    assert html == "<html>case card content</html>"


# ── progressive search error propagation (task §3) ──


def test_progressive_search_all_transport_failures_raises():
    """All attempts timeout → progressive_search raises, NOT return []."""
    from court_monitor.services.court_orchestrator import progressive_search  # noqa: PLC0415

    class _AlwaysTimeoutAdapter:
        def search(self, criteria):  # noqa: ARG002
            raise SudrfTemporaryError("timeout")

    with pytest.raises(SudrfTemporaryError):
        progressive_search(
            adapter=_AlwaysTimeoutAdapter(),
            court="c",
            article="205.1",
            result_date=date(2026, 4, 2),
            publication_date_hint=None,
            person_name=None,
        )


def test_progressive_search_all_blocked_raises():
    """All attempts blocked → raise SudrfBlockedError, NOT return []."""
    from court_monitor.services.court_orchestrator import progressive_search  # noqa: PLC0415

    class _AlwaysBlockedAdapter:
        def search(self, criteria):  # noqa: ARG002
            raise SudrfBlockedError("captcha")

    with pytest.raises(SudrfBlockedError):
        progressive_search(
            adapter=_AlwaysBlockedAdapter(),
            court="c",
            article="205.1",
            result_date=date(2026, 4, 2),
            publication_date_hint=None,
            person_name=None,
        )


def test_progressive_search_first_fail_second_success_zero_returns_empty():
    """First timeout, second success with 0 results → legitimate no-match."""
    from court_monitor.services.court_orchestrator import progressive_search  # noqa: PLC0415

    call_count = 0

    class _FailThenSucceedAdapter:
        def search(self, criteria):  # noqa: ARG002
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise SudrfTemporaryError("timeout")
            return []

    results, attempts = progressive_search(
        adapter=_FailThenSucceedAdapter(),
        court="c",
        article="205.1",
        result_date=date(2026, 4, 2),
        publication_date_hint=None,
        person_name=None,
    )
    assert results == []
    assert len(attempts) >= 2
    assert attempts[0].error is not None


def test_progressive_search_strategy_order():
    """article+result_date first, then article_only (not article+pub_date)."""
    from court_monitor.services.court_orchestrator import progressive_search  # noqa: PLC0415

    strategies_seen: list[str] = []

    class _RecordingAdapter:
        def search(self, criteria):  # noqa: ARG002
            if criteria.result_date is not None and criteria.person_name is None:
                strategies_seen.append("article_result_date")
            elif criteria.person_name is not None:
                strategies_seen.append("person_article")
            else:
                strategies_seen.append("article_only")
            return []

    progressive_search(
        adapter=_RecordingAdapter(),
        court="c",
        article="205.1",
        result_date=date(2026, 4, 2),
        publication_date_hint=date(2026, 4, 3),
        person_name="Иванов",
    )
    assert strategies_seen[0] == "article_result_date"
    assert strategies_seen[1] == "article_only"


# ── fixture transport strictness (task §6) ──


def test_fixture_transport_unknown_article_returns_404():
    """Unknown article → 404, NOT arbitrary first search-result fixture."""
    from urllib.parse import urlencode  # noqa: PLC0415

    from court_monitor.sources.fixture_transport import FixtureTransport  # noqa: PLC0415

    fixtures = Path("tests/fixtures/sudrf-live/2zovs/sud_delo")
    if not fixtures.exists():
        pytest.skip("2zovs sud_delo fixtures not available")

    transport = FixtureTransport(fixtures)
    url = "https://x/modules.php?" + urlencode(
        {
            "name": "sud_delo",
            "U1_DEFENDANT__LAW_ARTICLESS": "999.999",
        }
    )
    resp = transport.get(url)
    assert resp.status == 404
    assert resp.health == FetchHealth.http_error


def test_fixture_transport_unknown_case_uid_returns_404():
    """Unknown case_uid → 404, NOT case-card-example.html fallback."""
    from urllib.parse import urlencode  # noqa: PLC0415

    from court_monitor.sources.fixture_transport import FixtureTransport  # noqa: PLC0415

    fixtures = Path("tests/fixtures/sudrf-live/2zovs/sud_delo")
    if not fixtures.exists():
        pytest.skip("2zovs sud_delo fixtures not available")

    transport = FixtureTransport(fixtures)
    url = "https://x/modules.php?" + urlencode(
        {
            "name": "sud_delo",
            "name_op": "case",
            "case_uid": "unknown-uid-that-does-not-exist",
        }
    )
    resp = transport.get(url)
    assert resp.status == 404
    assert resp.health == FetchHealth.http_error


def test_fixture_transport_known_article_returns_exact_fixture():
    """Known article → exact search-result fixture."""
    from urllib.parse import urlencode  # noqa: PLC0415

    from court_monitor.sources.fixture_transport import FixtureTransport  # noqa: PLC0415

    fixtures = Path("tests/fixtures/sudrf-live/2zovs/sud_delo")
    if not fixtures.exists():
        pytest.skip("2zovs sud_delo fixtures not available")

    transport = FixtureTransport(fixtures)
    url = "https://x/modules.php?" + urlencode(
        {
            "name": "sud_delo",
            "U1_DEFENDANT__LAW_ARTICLESS": "205.1",
        }
    )
    resp = transport.get(url)
    assert resp.status == 200
    assert resp.health == FetchHealth.ok
    assert len(resp.text) > 100


def test_fixture_transport_known_case_uid_returns_exact_fixture():
    """Known case_uid → exact case-card fixture."""
    from urllib.parse import urlencode  # noqa: PLC0415

    from court_monitor.sources.fixture_transport import FixtureTransport  # noqa: PLC0415

    fixtures = Path("tests/fixtures/sudrf-live/2zovs/sud_delo")
    if not fixtures.exists():
        pytest.skip("2zovs sud_delo fixtures not available")

    transport = FixtureTransport(fixtures)
    url = "https://x/modules.php?" + urlencode(
        {
            "name": "sud_delo",
            "name_op": "case",
            "case_uid": "7c5401a4-de1d-497e-a034-7d6d3f6886a4",
        }
    )
    resp = transport.get(url)
    assert resp.status == 200
    assert resp.health == FetchHealth.ok
    assert len(resp.text) > 100


def test_fixture_transport_empty_result_fixture_exists():
    """search-result-empty.html fixture must exist for explicit empty searches."""
    fixtures = Path("tests/fixtures/sudrf-live/2zovs/sud_delo")
    if not fixtures.exists():
        pytest.skip("2zovs sud_delo fixtures not available")

    empty_fixture = fixtures / "search-result-empty.html"
    assert empty_fixture.exists(), "search-result-empty.html fixture is missing"

"""Adapter for searching cases on sudrf.ru courts.

Uses the standard GET search form on the live site, or a local fixture
transport for offline/test runs. Transport selection is determined by
``SourceConfig.backend``:

* ``fixture`` → :class:`FixtureTransport` reads local HTML (no network)
* ``http``    → live HTTP via :class:`HttpClient` (must be gated by ``--live``
  in the CLI)

The CLI command ``court-monitor find-case`` / ``process-court-cases`` only
flips the backend between those two values — it never branches on business
logic. This keeps the fixture path an exact functional mirror of the live
path.

All parameter names are the ones observed live on yovs on 2026-08-12 (see
``.cache/sudrf-live-discovery-2026-08-12.md``). The server treats parameter
names case-insensitively, but this adapter uses the upper-case form for
consistency with the discovery record.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

from court_monitor.config.loader import SourceConfig
from court_monitor.domain.models import FetchHealth, SourceBackend
from court_monitor.observability import get_logger
from court_monitor.parsers.sud_delo_list import parse_case_list
from court_monitor.sources.fixture_transport import FixtureTransport
from court_monitor.sources.http_client import HttpClient, HttpResponse
from court_monitor.sources.sudrf_dto import SudrfCaseSearchCriteria, SudrfCaseSearchResult

_log = get_logger(__name__)


class SudrfTransportError(Exception):
    """Base for transport-level failures during case search / card fetch."""


class SudrfTemporaryError(SudrfTransportError):
    """Transient failure (timeout, HTTP 5xx) — retry may succeed."""


class SudrfPermanentError(SudrfTransportError):
    """Non-retryable failure (HTTP 4xx except 403/429) — retry won't help."""


class SudrfBlockedError(SudrfTemporaryError):
    """The remote site returned a captcha / anti-bot wall (FetchHealth.blocked).

    Subclass of SudrfTemporaryError: blocked responses are retried up to
    MAX_ATTEMPTS (the site may clear the block).
    """


# Hidden form fields required for sud_delo search to return results.
# Verified live on yovs.ros.sudrf.ru; required even for empty criteria.
_SEARCH_BASE_PARAMS = {
    "name": "sud_delo",
    "srv_num": "1",
    "name_op": "r",
    "case_type": "0",
    "delo_table": "u1_case",
}


class SudrfCaseSearchAdapter:
    """Adapter for searching cases on sudrf.ru courts.

    Args:
        config: Court source config (``backend`` selects fixture/http).
        http_client_factory: Optional override for tests. When ``None`` the
            adapter chooses between :class:`HttpClient` (``backend=http``) and
            :class:`FixtureTransport` (``backend=fixture``) based on config.
    """

    def __init__(
        self,
        config: SourceConfig,
        *,
        http_client_factory: Callable[[], HttpClient | FixtureTransport] | None = None,
    ) -> None:
        self.config = config
        self.base_url = config.base_url or ""
        self.court_name = config.court_name or config.name
        self._http_client_factory = http_client_factory

    def search(self, criteria: SudrfCaseSearchCriteria) -> list[SudrfCaseSearchResult]:
        """Search for cases matching criteria.

        Sends a GET request to ``/modules.php`` with form parameters matching
        the browser form submission.

        Raises:
            SudrfBlockedError: site returned CAPTCHA / anti-bot wall.
            SudrfTemporaryError: timeout or transient HTTP error.
            SudrfTransportError: other non-200 or non-ok responses.
        """
        _log.info(
            "sudrf.search.start",
            court=self.court_name,
            backend=self.config.backend.value,
            article=criteria.article,
            result_date=criteria.result_date,
            event_date=criteria.event_date,
            case_number=criteria.case_number,
            person_name=criteria.person_name,
        )

        params = self._build_search_params(criteria)
        search_url = f"{self.base_url}/modules.php"

        with self._make_http_client() as client:
            response = client.get(f"{search_url}?{urlencode(params)}")
            _raise_on_transport_error(response, context="search")
            results = self._parse_search_response(response)

        _log.info(
            "sudrf.search.results",
            court=self.court_name,
            total=len(results),
        )
        return results[: criteria.limit]

    def fetch_case_card_html(self, result: SudrfCaseSearchResult) -> str | None:
        """Fetch HTML content of a case card.

        Raises:
            SudrfBlockedError: site returned CAPTCHA / anti-bot wall.
            SudrfTemporaryError: timeout or transient HTTP error.
            SudrfTransportError: other non-200 or non-ok responses.
        """
        _log.info(
            "sudrf.case_card.fetch",
            backend=self.config.backend.value,
            case_uid=result.case_uid,
            url=result.url,
        )

        with self._make_http_client() as client:
            response = client.get(result.url.replace(" ", "%20"))

        _raise_on_transport_error(response, context="case_card")
        return response.text

    def _make_http_client(self) -> HttpClient | FixtureTransport:
        """Build the appropriate transport from ``config.backend``.

        The optional ``http_client_factory`` override (used by tests) wins over
        the config, so a test can inject a fake transport even when
        ``backend`` is set to ``http``.
        """
        if self._http_client_factory is not None:
            return self._http_client_factory()
        if self.config.backend == SourceBackend.fixture:
            fixture_path = self.config.fixture_path or ""
            # Case fixtures live next to press fixtures:
            # ``tests/fixtures/sudrf-live/{court}/sud_delo/``
            if not fixture_path.endswith("sud_delo"):
                root = Path(fixture_path).parent / "sud_delo"
                fixture_path = str(root)
            return FixtureTransport(fixture_path)
        return HttpClient()

    def _parse_search_response(self, response: HttpResponse) -> list[SudrfCaseSearchResult]:
        if response.status != 200 or not response.text:
            _log.error(
                "sudrf.search.http_error",
                status=response.status,
            )
            return []
        parsed = parse_case_list(response.text, self.base_url)
        return parsed.results

    def _build_search_params(self, criteria: SudrfCaseSearchCriteria) -> dict[str, str]:
        """Build search query parameters matching the browser form.

        Verified live on yovs.ros.sudrf.ru (2026-08-12):
        * ``U1_DEFENDANT__LAW_ARTICLESS`` — article (e.g. ``205.1``)
        * ``U1_CASE__CASE_NUMBERSS``      — case number (e.g. ``1-1688/2026``)
        * ``U1_DEFENDANT__NAMESS``        — defendant name
        * ``U1_CASE__RESULT_DATE1D/2D``   — result date range (verdict date)
        * ``U1_EVENT__EVENT_DATEDD``      — event date (NOT the verdict date)

        ``result_date`` maps to ``RESULT_DATE*`` — the field the form uses for
        "рассмотрено YYYY-MM-DD". ``event_date`` maps to ``EVENT_DATEDD`` — the
        "event happened on" field, which returns all cases with any event on
        that day and must not be used as a verdict-date filter.
        """
        params: dict[str, str] = dict(_SEARCH_BASE_PARAMS)
        params["delo_id"] = criteria.delo_id or "1540006"
        params["new"] = criteria.new_flag or "0"

        if criteria.article:
            params["U1_DEFENDANT__LAW_ARTICLESS"] = criteria.article
        if criteria.case_number:
            params["U1_CASE__CASE_NUMBERSS"] = criteria.case_number
        if criteria.person_name:
            params["U1_DEFENDANT__NAMESS"] = criteria.person_name
        if criteria.result_date:
            params["U1_CASE__RESULT_DATE1D"] = criteria.result_date.strftime("%d.%m.%Y")
            params["U1_CASE__RESULT_DATE2D"] = criteria.result_date.strftime("%d.%m.%Y")
        if criteria.event_date:
            params["U1_EVENT__EVENT_DATEDD"] = criteria.event_date.strftime("%d.%m.%Y")

        return params


def _raise_on_transport_error(response: HttpResponse, *, context: str) -> None:
    """Raise a typed exception if the response indicates a transport failure.

    Only ``status == 200`` AND ``health == FetchHealth.ok`` is considered a
    successful response. Everything else maps to a typed exception so that
    callers (orchestrator, progressive_search) can distinguish transport
    failures from legitimate empty results.

    Error classification:
      * timeout / status=0 → SudrfTemporaryError (retry may succeed)
      * HTTP 5xx → SudrfTemporaryError
      * 403 / 429 / blocked → SudrfBlockedError (retry up to MAX_ATTEMPTS)
      * other 4xx (400, 404…) → SudrfPermanentError (retry won't help)
      * other non-200 → SudrfPermanentError
    """
    if response.health == FetchHealth.blocked:
        raise SudrfBlockedError(f"sudrf.{context}: blocked (captcha/anti-bot) at {response.url}")
    if response.health == FetchHealth.timeout:
        raise SudrfTemporaryError(f"sudrf.{context}: timeout at {response.url}")
    if response.health == FetchHealth.http_error:
        status = response.status
        if status >= 500:
            raise SudrfTemporaryError(f"sudrf.{context}: HTTP {status} at {response.url}")
        if status in (403, 429):
            raise SudrfBlockedError(f"sudrf.{context}: HTTP {status} at {response.url}")
        raise SudrfPermanentError(f"sudrf.{context}: HTTP {status} at {response.url}")
    if response.status != 200:
        raise SudrfPermanentError(
            f"sudrf.{context}: unexpected status {response.status} at {response.url}"
        )

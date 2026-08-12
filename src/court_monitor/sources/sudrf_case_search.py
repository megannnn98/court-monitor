"""Adapter for searching cases on sudrf.ru courts via real HTTP search.

Uses the standard GET search form — JavaScript is NOT required.
"""

from __future__ import annotations

from court_monitor.config.loader import SourceConfig
from court_monitor.domain.models import FetchHealth
from court_monitor.observability import get_logger
from court_monitor.parsers.sud_delo_list import parse_case_list
from court_monitor.sources.http_client import HttpClient
from court_monitor.sources.sudrf_dto import SudrfCaseSearchCriteria, SudrfCaseSearchResult

_log = get_logger(__name__)

# Hidden form fields required for the search to work
_SEARCH_BASE_PARAMS = {
    "name": "sud_delo",
    "srv_num": "1",
    "name_op": "r",
    "case_type": "0",
    "delo_table": "u1_case",
}


class SudrfCaseSearchAdapter:
    """Adapter for searching cases on sudrf.ru courts via HTTP GET.

    The sud_delo search form works with standard HTTP GET — no JavaScript
    required. Hidden fields must be included for the search to return results.
    """

    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.base_url = config.base_url or ""
        self.court_name = config.court_name or config.name

    def search(self, criteria: SudrfCaseSearchCriteria) -> list[SudrfCaseSearchResult]:
        """Search for cases matching criteria via real sud_delo HTTP search.

        Sends a GET request to /modules.php with form parameters matching
        the browser form submission.
        """
        _log.info(
            "sudrf.search.start",
            court=self.court_name,
            article=criteria.article,
            decision_date=criteria.decision_date,
            case_number=criteria.case_number,
            person_name=criteria.person_name,
        )

        params = self._build_search_params(criteria)
        search_url = f"{self.base_url}/modules.php"

        with HttpClient() as client:
            response = client.get(f"{search_url}?{'&'.join(f'{k}={v}' for k, v in params.items())}")

            if response.status != 200 or response.health != FetchHealth.ok:
                _log.error(
                    "sudrf.search.http_error",
                    url=search_url,
                    status=response.status,
                    health=str(response.health),
                )
                return []

            parsed = parse_case_list(response.text, self.base_url)

            _log.info(
                "sudrf.search.results",
                court=self.court_name,
                total=len(parsed.results),
            )

            return parsed.results[: criteria.limit]

    def _build_search_params(self, criteria: SudrfCaseSearchCriteria) -> dict[str, str]:
        """Build search query parameters matching the browser form."""
        params: dict[str, str] = dict(_SEARCH_BASE_PARAMS)
        params["delo_id"] = criteria.delo_id or "1540006"
        params["new"] = criteria.new_flag or "0"

        if criteria.article:
            params["U1_DEFENDANT__LAW_ARTICLESS"] = criteria.article
        if criteria.case_number:
            params["U1_CASE__CASE_NUMBERSS"] = criteria.case_number
        if criteria.person_name:
            params["U1_DEFENDANT__NAMESS"] = criteria.person_name
        if criteria.decision_date:
            params["U1_EVENT__EVENT_DATEDD"] = criteria.decision_date.strftime("%d.%m.%Y")

        return params

    def fetch_case_card_html(self, result: SudrfCaseSearchResult) -> str | None:
        """Fetch HTML content of a case card."""
        _log.info(
            "sudrf.case_card.fetch",
            case_uid=result.case_uid,
            url=result.url,
        )

        with HttpClient() as client:
            response = client.get(result.url)

            if response.status != 200 or response.health != FetchHealth.ok:
                _log.error(
                    "sudrf.case_card.http_error",
                    url=result.url,
                    status=response.status,
                    health=str(response.health),
                )
                return None

            return response.text

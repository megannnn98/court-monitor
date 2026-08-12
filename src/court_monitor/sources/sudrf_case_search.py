"""Adapter for searching cases on sudrf.ru courts."""

from __future__ import annotations

from court_monitor.config.loader import SourceConfig
from court_monitor.domain.models import FetchHealth
from court_monitor.observability import get_logger
from court_monitor.parsers.sud_delo_list import parse_case_list
from court_monitor.sources.http_client import HttpClient
from court_monitor.sources.sudrf_dto import SudrfCaseSearchCriteria, SudrfCaseSearchResult

_log = get_logger(__name__)


class SudrfCaseSearchAdapter:
    """Adapter for searching cases on sudrf.ru courts.

    Current implementation: parses case list from main sud_delo page.
    This is a workaround because the search form requires JavaScript.

    Future: implement full search via Playwright or JavaScript emulation.
    """

    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.base_url = config.base_url or ""
        self.court_name = config.court_name or config.name

    def search(self, criteria: SudrfCaseSearchCriteria) -> list[SudrfCaseSearchResult]:
        """Search for cases matching criteria.

        Current implementation: fetches case list from main sud_delo page
        and filters by criteria locally.

        Args:
            criteria: Search criteria

        Returns:
            List of matching case search results
        """
        _log.info(
            "sudrf.search.start",
            court=self.court_name,
            article=criteria.article,
            decision_date=criteria.decision_date,
        )

        # Fetch case list from main sud_delo page
        # NOTE: This is a workaround — the page only shows cases scheduled for
        # today. Past decisions will not be found. Full search requires
        # Playwright (see docs/sudrf-case-search-discovery.md).
        case_list_url = f"{self.base_url}/modules.php?name=sud_delo&srv_num=1"

        with HttpClient() as client:
            response = client.get(case_list_url)

            if response.status != 200 or response.health != FetchHealth.ok:
                _log.error(
                    "sudrf.search.http_error",
                    url=case_list_url,
                    status=response.status,
                    health=str(response.health),
                )
                return []

            # Parse case list
            parsed = parse_case_list(response.text, self.base_url)

            _log.info(
                "sudrf.search.parsed",
                court=self.court_name,
                total_cases=len(parsed.results),
            )

            # Filter by criteria (local filtering for now)
            filtered = self._filter_results(parsed.results, criteria)

            _log.info(
                "sudrf.search.filtered",
                court=self.court_name,
                filtered_count=len(filtered),
            )

            return filtered[: criteria.limit]

    def _filter_results(
        self,
        results: list[SudrfCaseSearchResult],
        criteria: SudrfCaseSearchCriteria,
    ) -> list[SudrfCaseSearchResult]:
        """Filter results by criteria.

        Current implementation: basic filtering by case number.
        Full filtering requires fetching and parsing case cards.
        """
        filtered = results

        # Filter by case number if specified
        if criteria.case_number:
            filtered = [
                r for r in filtered if r.case_number and criteria.case_number in r.case_number
            ]

        # Note: article and date filtering require fetching case cards,
        # which is done in the orchestrator after this search.

        return filtered

    def fetch_case_card_html(self, result: SudrfCaseSearchResult) -> str | None:
        """Fetch HTML content of a case card.

        Args:
            result: Search result with case card URL

        Returns:
            HTML content or None if fetch failed
        """
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

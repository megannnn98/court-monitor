"""Parser for sud_delo case list and search results."""

from __future__ import annotations

import re
from dataclasses import dataclass

from selectolax.parser import HTMLParser

from court_monitor.sources.sudrf_dto import SudrfCaseSearchResult

# Regex to extract case card URL parameters
_CASE_URL_RE = re.compile(
    r"name=sud_delo.*?case_id=(\d+).*?case_uid=([a-f0-9-]+).*?delo_id=(\d+)",
    re.IGNORECASE,
)

# Regex to extract case number from link text
_CASE_NUMBER_RE = re.compile(r"(\d+-\d+/\d+|\d+К-\d+/\d+)")


@dataclass
class ParsedCaseList:
    """Parsed list of cases from sud_delo page."""

    results: list[SudrfCaseSearchResult]
    total_count: int | None = None


def parse_case_list(html: str, base_url: str) -> ParsedCaseList:
    """Parse case list from sud_delo main page or search results.

    Args:
        html: HTML content of the page
        base_url: Base URL of the court (e.g., "https://2zovs.msk.sudrf.ru")

    Returns:
        ParsedCaseList with extracted case links
    """
    tree = HTMLParser(html)
    results: list[SudrfCaseSearchResult] = []

    # Find all links to case cards
    for node in tree.css("a[href*='name=sud_delo'][href*='name_op=case']"):
        href = node.attributes.get("href", "")
        if not href:
            continue

        # Extract parameters from URL
        match = _CASE_URL_RE.search(href)
        if not match:
            continue

        case_id = match.group(1)
        case_uid = match.group(2)
        delo_id = match.group(3)

        # Extract srv_num from URL (usually 1)
        srv_num_match = re.search(r"srv_num=(\d+)", href)
        srv_num = srv_num_match.group(1) if srv_num_match else "1"

        # Extract case number from link text
        link_text = node.text(strip=True)
        case_number_match = _CASE_NUMBER_RE.search(link_text)
        case_number = case_number_match.group(1) if case_number_match else None

        # Build full URL
        full_url = href if href.startswith("http") else f"{base_url}{href}"

        results.append(
            SudrfCaseSearchResult(
                case_number=case_number,
                case_id=case_id,
                case_uid=case_uid,
                delo_id=delo_id,
                srv_num=srv_num,
                url=full_url,
            )
        )

    return ParsedCaseList(results=results, total_count=len(results))

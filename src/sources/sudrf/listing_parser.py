from selectolax.parser import HTMLParser

from sources.models import SourceReference
from sources.sudrf.reference import (
    LISTING_QUERY,
    archive_year,
    canonicalize_sudrf_reference,
    court_url,
)


class SudrfListingParser:
    """Rows of the press-service news table, plus the links to its per-year archives."""

    # The latest-news page and an archive page use different container ids.
    ROW_LINK_SELECTOR = "#divNewsList a, #divArchiveList a"
    ARCHIVE_LINK_SELECTOR = "#divArchiveSelector a"

    def __init__(self, host: str) -> None:
        self._host = host

    def parse(self, html: bytes) -> list[SourceReference]:
        references: list[SourceReference] = []
        seen_external_ids: set[str] = set()

        for href in self._hrefs(html, self.ROW_LINK_SELECTOR):
            reference = canonicalize_sudrf_reference(self._host, href)

            if reference is None:
                continue

            if reference.external_id in seen_external_ids:
                continue

            seen_external_ids.add(reference.external_id)
            references.append(reference)

        return references

    def parse_archive_years(self, html: bytes) -> list[str]:
        """Archive page URLs, newest year first; one page holds a whole year of news."""
        years: set[str] = set()

        for href in self._hrefs(html, self.ARCHIVE_LINK_SELECTOR):
            year = archive_year(self._host, href)

            if year is not None:
                years.add(year)

        return [
            court_url(self._host, f"{LISTING_QUERY}&op=12&arc_list={year}")
            for year in sorted(years, key=int, reverse=True)
        ]

    @staticmethod
    def _hrefs(html: bytes, selector: str) -> list[str]:
        return [
            href
            for node in HTMLParser(html).css(selector)
            if (href := node.attributes.get("href")) is not None
        ]

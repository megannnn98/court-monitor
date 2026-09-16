from pathlib import Path

from sources.models import SourceReference
from sources.sudrf.listing_parser import SudrfListingParser

HOST = "2zovs.msk.sudrf.ru"
LISTING_PATH = Path("tests/fixtures/sudrf_listing.html")
ARCHIVE_PATH = Path("tests/fixtures/sudrf_archive_2026.html")


def test_parse_extracts_news_references_from_the_latest_news_page() -> None:
    references = SudrfListingParser(HOST).parse(LISTING_PATH.read_bytes())

    assert len(references) == 30
    assert references[0] == SourceReference(
        external_id="397",
        url="https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=1&did=397",
    )
    assert references[-1] == SourceReference(
        external_id="368",
        url="https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=1&did=368",
    )


def test_parse_extracts_news_references_from_an_archive_page() -> None:
    """An archive page lists a whole year and uses a different container id."""
    references = SudrfListingParser(HOST).parse(ARCHIVE_PATH.read_bytes())

    external_ids = [reference.external_id for reference in references]

    assert len(external_ids) == 245
    assert external_ids[:2] == ["397", "396"]
    assert {"364", "369"} <= set(external_ids)


def test_parse_ignores_navigation_and_archive_links() -> None:
    html = b"""
        <div id='divArchiveSelector'>
            <a href='/modules.php?name=press_dep&op=12&arc_list=2026'>2026</a>
        </div>
        <div id='divNewsList'>
            <a href='/modules.php?name=press_dep&op=7'>Contacts</a>
            <a href='http://vkks.ru'>External</a>
            <a href='/modules.php?name=press_dep&op=1&did=12'>News</a>
        </div>
    """

    references = SudrfListingParser(HOST).parse(html)

    assert [reference.external_id for reference in references] == ["12"]


def test_parse_deduplicates_references_preserving_order() -> None:
    html = b"""
        <div id='divNewsList'>
            <a href='/modules.php?name=press_dep&op=1&did=2'>B</a>
            <a href='/modules.php?name=press_dep&op=1&did=1'>A</a>
            <a href='/modules.php?name=press_dep&op=1&did=2'>B again</a>
        </div>
    """

    references = SudrfListingParser(HOST).parse(html)

    assert [reference.external_id for reference in references] == ["2", "1"]


def test_parse_archive_years_returns_year_pages_newest_first() -> None:
    urls = SudrfListingParser(HOST).parse_archive_years(LISTING_PATH.read_bytes())

    assert urls == [
        "https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=12&arc_list=2026",
        "https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=12&arc_list=2025",
        "https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=12&arc_list=2024",
    ]


def test_parse_archive_years_ignores_per_month_links() -> None:
    """Months are subsets of their year page, so fetching them would only repeat work."""
    html = b"""
        <div id='divArchiveSelector'>
            <a href='/modules.php?name=press_dep&op=12&arc_list=2026'>2026</a>
            <a href='/modules.php?name=press_dep&op=12&arc_list=2026-09'>September</a>
            <a href='/modules.php?name=press_dep&op=12&arc_list=2026-08'>August</a>
        </div>
        <div id='divNewsList'>
            <a href='/modules.php?name=press_dep&op=12&arc_list=1999'>Outside the selector</a>
        </div>
    """

    urls = SudrfListingParser(HOST).parse_archive_years(html)

    assert urls == [
        "https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=12&arc_list=2026",
    ]


def test_parse_archive_years_survives_hrefs_with_a_tail_after_the_year() -> None:
    """The court's template could append anything; a year must still be found."""
    html = b"""
        <div id='divArchiveSelector'>
            <a href='/modules.php?name=press_dep&op=12&arc_list=2026&print=1'>2026</a>
            <a href='/modules.php?name=press_dep&op=12&arc_list=2025#top'>2025</a>
            <a href=' /modules.php?name=press_dep&op=12&arc_list=2024 '>2024</a>
        </div>
    """

    urls = SudrfListingParser(HOST).parse_archive_years(html)

    assert urls == [
        "https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=12&arc_list=2026",
        "https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=12&arc_list=2025",
        "https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=12&arc_list=2024",
    ]


def test_parse_archive_years_deduplicates_a_year_linked_twice() -> None:
    html = b"""
        <div id='divArchiveSelector'>
            <a href='/modules.php?name=press_dep&op=12&arc_list=2026'>2026</a>
            <a href='/modules.php?name=press_dep&op=12&arc_list=2026&print=1'>2026 print</a>
        </div>
    """

    assert len(SudrfListingParser(HOST).parse_archive_years(html)) == 1

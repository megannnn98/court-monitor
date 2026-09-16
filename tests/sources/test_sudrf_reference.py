import pytest

from sources.models import SourceReference
from sources.sudrf.reference import archive_year, canonicalize_sudrf_reference

HOST = "2zovs.msk.sudrf.ru"


def test_canonicalize_builds_absolute_reference_from_a_listing_href() -> None:
    reference = canonicalize_sudrf_reference(HOST, "/modules.php?name=press_dep&op=1&did=369")

    assert reference == SourceReference(
        external_id="369",
        url="https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=1&did=369",
    )


def test_canonicalize_accepts_the_double_dash_host_the_court_redirects_from() -> None:
    """Links are published as `2zovs--msk.sudrf.ru`, which 301s to `2zovs.msk.sudrf.ru`."""
    reference = canonicalize_sudrf_reference(
        HOST,
        "https://2zovs--msk.sudrf.ru/modules.php?name=press_dep&op=1&did=369",
    )

    assert reference == SourceReference(
        external_id="369",
        url="https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=1&did=369",
    )


def test_canonicalize_drops_extra_query_parameters() -> None:
    reference = canonicalize_sudrf_reference(
        HOST,
        "/modules.php?name=press_dep&op=1&did=369&ysclid=abc#top",
    )

    assert reference == SourceReference(
        external_id="369",
        url="https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=1&did=369",
    )


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("/modules.php?name=press_dep", id="listing itself"),
        pytest.param("/modules.php?name=press_dep&op=12&arc_list=2026", id="archive page"),
        pytest.param("/modules.php?name=press_dep&op=7", id="contacts page"),
        pytest.param("/modules.php?name=sud_delo&op=1&did=369", id="other module"),
        pytest.param("/modules.php?name=press_dep&op=1", id="news item without did"),
        pytest.param("/modules.php?name=press_dep&op=1&did=latest", id="non-numeric did"),
        pytest.param("/index.php?did=369", id="other path"),
        pytest.param("https://vkks.ru/modules.php?name=press_dep&op=1&did=369", id="other host"),
        pytest.param(
            "https://1zovs.msk.sudrf.ru/modules.php?name=press_dep&op=1&did=369",
            id="another court",
        ),
    ],
)
def test_canonicalize_rejects_anything_that_is_not_a_news_item_of_this_court(url: str) -> None:
    assert canonicalize_sudrf_reference(HOST, url) is None


@pytest.mark.parametrize(
    "url",
    [
        pytest.param(
            "https://2zovs.msk.sudrf--ru/modules.php?name=press_dep&op=1&did=369",
            id="separately registrable near-host",
        ),
        pytest.param(
            "https://2zovs--msk--sudrf--ru/modules.php?name=press_dep&op=1&did=369",
            id="every dot replaced",
        ),
    ],
)
def test_canonicalize_rejects_a_host_that_only_looks_like_the_court(url: str) -> None:
    """`2zovs.msk.sudrf--ru` is someone else's domain, not the court's legacy alias."""
    assert canonicalize_sudrf_reference(HOST, url) is None


def test_canonicalize_accepts_the_court_host_in_upper_case() -> None:
    reference = canonicalize_sudrf_reference(
        HOST,
        "https://2ZOVS.MSK.SUDRF.RU/modules.php?name=press_dep&op=1&did=369",
    )

    assert reference == SourceReference(
        external_id="369",
        url="https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=1&did=369",
    )


def test_archive_year_reads_the_year_of_an_archive_page() -> None:
    assert archive_year(HOST, "/modules.php?name=press_dep&op=12&arc_list=2026") == "2026"


@pytest.mark.parametrize(
    "url",
    [
        pytest.param(
            "/modules.php?name=press_dep&op=12&arc_list=2026&print=1",
            id="extra parameter after the year",
        ),
        pytest.param("/modules.php?name=press_dep&op=12&arc_list=2026#top", id="fragment"),
        pytest.param(" /modules.php?name=press_dep&op=12&arc_list=2026 ", id="surrounding spaces"),
        pytest.param("/modules.php?op=12&arc_list=2026&name=press_dep", id="reordered parameters"),
    ],
)
def test_archive_year_survives_hrefs_the_year_is_not_the_last_thing_in(url: str) -> None:
    """A regex anchored at the end of the href would silently skip these."""
    assert archive_year(HOST, url) == "2026"


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("/modules.php?name=press_dep&op=12&arc_list=2026-09", id="month page"),
        pytest.param("/modules.php?name=press_dep&op=12", id="no arc_list"),
        pytest.param("/modules.php?name=press_dep&op=1&did=369", id="news item"),
        pytest.param("/modules.php?name=sud_delo&op=12&arc_list=2026", id="other module"),
        pytest.param("/modules.php?name=press_dep&op=12&arc_list=26", id="two-digit year"),
        pytest.param(
            "https://2zovs.msk.sudrf--ru/modules.php?name=press_dep&op=12&arc_list=2026",
            id="near-host",
        ),
    ],
)
def test_archive_year_rejects_anything_that_is_not_a_year_archive(url: str) -> None:
    assert archive_year(HOST, url) is None

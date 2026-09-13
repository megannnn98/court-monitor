from models import SourceReference
from ovd_info_reference import canonicalize_ovd_info_reference


def test_canonicalizes_relative_path() -> None:
    reference = canonicalize_ovd_info_reference("/express-news/2026/09/01/article-a")

    assert reference == SourceReference(
        external_id="/express-news/2026/09/01/article-a",
        url="https://ovd.info/express-news/2026/09/01/article-a",
    )


def test_strips_query_and_fragment() -> None:
    reference = canonicalize_ovd_info_reference(
        "https://ovd.info/express-news/2026/09/01/article-a?utm_source=test#comments"
    )

    assert reference == SourceReference(
        external_id="/express-news/2026/09/01/article-a",
        url="https://ovd.info/express-news/2026/09/01/article-a",
    )


def test_rejects_other_domain() -> None:
    reference = canonicalize_ovd_info_reference(
        "https://example.com/express-news/2026/09/01/article-a"
    )

    assert reference is None


def test_rejects_non_article_path() -> None:
    reference = canonicalize_ovd_info_reference("https://ovd.info/persons/person-a")

    assert reference is None


def test_direct_url_and_discovered_href_produce_same_reference() -> None:
    direct_url = "https://ovd.info/express-news/2026/09/01/article-a?utm_source=share#top"
    discovered_href = "/express-news/2026/09/01/article-a"

    direct_reference = canonicalize_ovd_info_reference(direct_url)
    discovered_reference = canonicalize_ovd_info_reference(discovered_href)

    assert direct_reference is not None
    assert direct_reference == discovered_reference

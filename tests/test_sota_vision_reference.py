from models import SourceReference
from sota_vision_reference import canonicalize_sota_vision_reference


def test_canonicalizes_relative_path() -> None:
    reference = canonicalize_sota_vision_reference("/rossiya-zakryvaet-genkonsulstvo/")

    assert reference == SourceReference(
        external_id="/rossiya-zakryvaet-genkonsulstvo/",
        url="https://sota.vision/rossiya-zakryvaet-genkonsulstvo/",
    )


def test_strips_query_and_fragment() -> None:
    reference = canonicalize_sota_vision_reference(
        "https://sota.vision/rossiya-zakryvaet-genkonsulstvo/?utm_source=test#comments"
    )

    assert reference == SourceReference(
        external_id="/rossiya-zakryvaet-genkonsulstvo/",
        url="https://sota.vision/rossiya-zakryvaet-genkonsulstvo/",
    )


def test_rejects_other_domain() -> None:
    reference = canonicalize_sota_vision_reference(
        "https://example.com/rossiya-zakryvaet-genkonsulstvo/"
    )

    assert reference is None


def test_rejects_root_path() -> None:
    assert canonicalize_sota_vision_reference("https://sota.vision/") is None
    assert canonicalize_sota_vision_reference("https://sota.vision") is None


def test_rejects_category_and_pagination_paths() -> None:
    assert canonicalize_sota_vision_reference("https://sota.vision/category/news/") is None
    assert canonicalize_sota_vision_reference("https://sota.vision/category/news/page/2/") is None
    assert canonicalize_sota_vision_reference("https://sota.vision/tag/covid-19/") is None


def test_direct_url_and_discovered_href_produce_same_reference() -> None:
    direct_url = "https://sota.vision/rossiya-zakryvaet-genkonsulstvo/?utm_source=share#top"
    discovered_href = "/rossiya-zakryvaet-genkonsulstvo/"

    direct_reference = canonicalize_sota_vision_reference(direct_url)
    discovered_reference = canonicalize_sota_vision_reference(discovered_href)

    assert direct_reference is not None
    assert direct_reference == discovered_reference

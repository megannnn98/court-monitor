from pathlib import Path

from sources.models import SourceReference
from sources.sota_vision.listing_parser import SotaVisionListingParser

FIXTURE_PATH = Path("tests/fixtures/sota_vision_listing.html")


def test_parse_extracts_article_references() -> None:
    html = FIXTURE_PATH.read_bytes()

    references = SotaVisionListingParser().parse(html)

    assert references[:3] == [
        SourceReference(
            external_id="/rossiya-zakryvaet-genkonsulstvo-germanii-v-sankt-peterburge/",
            url="https://sota.vision/rossiya-zakryvaet-genkonsulstvo-germanii-v-sankt-peterburge/",
        ),
        SourceReference(
            external_id="/czifrovoj-rubl-budet-yarko-krasnogo-dofaminovogo-czveta-czb/",
            url="https://sota.vision/czifrovoj-rubl-budet-yarko-krasnogo-dofaminovogo-czveta-czb/",
        ),
        SourceReference(
            external_id="/czik-pokazal-byulleten-na-vyborah-v-gosdumu/",
            url="https://sota.vision/czik-pokazal-byulleten-na-vyborah-v-gosdumu/",
        ),
    ]


def test_parse_deduplicates_references_preserving_order() -> None:
    html = b"""
        <html>
            <body>
                <h2 class="entry-title"><a href="/article-a/">A</a></h2>
                <h2 class="entry-title"><a href="/article-b/">B</a></h2>
                <div class="post-thumbnail"><a href="/article-a/"><img src="x.jpg"></a></div>
                <h2 class="entry-title"><a href="/article-a/">A duplicate</a></h2>
            </body>
        </html>
    """

    references = SotaVisionListingParser().parse(html)

    assert [reference.external_id for reference in references] == [
        "/article-a/",
        "/article-b/",
    ]


def test_parse_removes_query_and_fragment_from_article_url() -> None:
    html = b"""
        <h2 class="entry-title">
            <a href="/article-a/?utm_source=test#comments">Article</a>
        </h2>
    """

    references = SotaVisionListingParser().parse(html)

    assert references == [
        SourceReference(
            external_id="/article-a/",
            url="https://sota.vision/article-a/",
        )
    ]


def test_parse_ignores_non_article_links() -> None:
    html = b"""
        <html>
            <body>
                <h2 class="entry-title">Section heading</h2>
                <a href="/category/news/">Category</a>
                <a href="/category/news/page/2/">Older</a>
                <a href="https://example.com/article-a/">External</a>
                <h2 class="entry-title"><a href="/article-b/">Article</a></h2>
            </body>
        </html>
    """

    references = SotaVisionListingParser().parse(html)

    assert references == [
        SourceReference(
            external_id="/article-b/",
            url="https://sota.vision/article-b/",
        )
    ]

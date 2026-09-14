from pathlib import Path

from sources.models import SourceReference
from sources.ovd_info.listing_parser import OvdInfoListingParser

FIXTURE_PATH = Path("tests/fixtures/ovd_info_listing.html")


def test_parse_extracts_article_references() -> None:
    html = FIXTURE_PATH.read_bytes()

    references = OvdInfoListingParser().parse(html)

    assert references[:5] == [
        SourceReference(
            external_id=(
                "/express-news/2026/09/11/"
                "minyust-isklyuchil-evgeniya-ponasenkova-iz-reestra-inoagentov"
            ),
            url=(
                "https://ovd.info/express-news/2026/09/11/"
                "minyust-isklyuchil-evgeniya-ponasenkova-iz-reestra-inoagentov"
            ),
        ),
        SourceReference(
            external_id=(
                "/express-news/2026/09/11/"
                "razrabotchik-vyvez-iz-rossii-vnutrennie-dokumenty-o-slezhke-fsb-chto-iz"
            ),
            url=(
                "https://ovd.info/express-news/2026/09/11/"
                "razrabotchik-vyvez-iz-rossii-vnutrennie-dokumenty-o-slezhke-fsb-chto-iz"
            ),
        ),
        SourceReference(
            external_id=(
                "/express-news/2026/09/02/"
                "v-moskve-zaderzhali-glavu-fonda-dom-s-mayakom-lidu-moniavu-pered-etim-u-nee"
            ),
            url=(
                "https://ovd.info/express-news/2026/09/02/"
                "v-moskve-zaderzhali-glavu-fonda-dom-s-mayakom-lidu-moniavu-pered-etim-u-nee"
            ),
        ),
        SourceReference(
            external_id=(
                "/express-news/2026/09/01/"
                "khakery-poluchili-imeyly-donorov-proekta-davayte-i-marafona-v-podderzhku"
            ),
            url=(
                "https://ovd.info/express-news/2026/09/01/"
                "khakery-poluchili-imeyly-donorov-proekta-davayte-i-marafona-v-podderzhku"
            ),
        ),
        SourceReference(
            external_id=(
                "/express-news/2026/08/31/"
                "zashchitnicu-azata-miftakhova-obvinili-v-antiobshchestvennom-povedenii-vo"
            ),
            url=(
                "https://ovd.info/express-news/2026/08/31/"
                "zashchitnicu-azata-miftakhova-obvinili-v-antiobshchestvennom-povedenii-vo"
            ),
        ),
    ]


def test_parse_deduplicates_references_preserving_order() -> None:
    html = b"""
        <html>
            <body>
                <a href="/express-news/2026/09/01/article-a">A</a>
                <a href="/express-news/2026/09/02/article-b">B</a>
                <a href="/express-news/2026/09/01/article-a">A duplicate</a>
            </body>
        </html>
    """

    references = OvdInfoListingParser().parse(html)

    assert [reference.external_id for reference in references] == [
        "/express-news/2026/09/01/article-a",
        "/express-news/2026/09/02/article-b",
    ]


def test_parse_removes_query_and_fragment_from_article_url() -> None:
    html = b"""
        <a href="/express-news/2026/09/01/article-a?utm_source=test#comments">
            Article
        </a>
    """

    references = OvdInfoListingParser().parse(html)

    assert references == [
        SourceReference(
            external_id="/express-news/2026/09/01/article-a",
            url="https://ovd.info/express-news/2026/09/01/article-a",
        )
    ]


def test_parse_ignores_non_article_links() -> None:
    html = b"""
        <html>
            <body>
                <a href="/">Home</a>
                <a href="/express-news">Express news</a>
                <a href="/persons/person-a">Person</a>
                <a href="/en/express-news/2026/09/01/article-a">English</a>
                <a href="https://example.com/express-news/2026/09/01/article-a">
                    External
                </a>
                <a href="/express-news/2026/09/01/article-b">Article</a>
            </body>
        </html>
    """

    references = OvdInfoListingParser().parse(html)

    assert references == [
        SourceReference(
            external_id="/express-news/2026/09/01/article-b",
            url="https://ovd.info/express-news/2026/09/01/article-b",
        )
    ]

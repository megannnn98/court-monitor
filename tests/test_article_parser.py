from pathlib import Path
from datetime import datetime, UTC

from article_parser import OvdInfoArticleParser
from models import RawDocument

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ovd_info_article.html"


def test_parser_extracts_article():
    html_bytes = FIXTURE_PATH.read_bytes()
    assert html_bytes

    raw_document = RawDocument(
        external_id="test-article",
        url="https://example.test/article",
        fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
        content_type="text/html; charset=UTF-8",
        content=html_bytes,
    )
    parser = OvdInfoArticleParser()
    article = parser.parse(raw_document)
    paragraphs = article.text.split("\n\n")

    assert article.external_id == raw_document.external_id
    assert article.url == raw_document.url

    assert article.title == ("На комика Сашу Долгополова завели дело о реабилитации нацизма")
    assert article.published_at == datetime(
        2026,
        8,
        13,
        17,
        57,
    )

    assert len(paragraphs) == 3
    assert paragraphs[0].startswith("На ходатайство о заочном аресте")
    assert all(paragraphs)

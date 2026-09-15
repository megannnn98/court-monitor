from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from sources.article_parser import OvdInfoArticleParser
from sources.ingestion_errors import ParseError
from sources.models import RawDocument

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "ovd_info_article.html"


def test_parser_extracts_article() -> None:
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
        tzinfo=ZoneInfo("Europe/Moscow"),
    )

    assert len(paragraphs) == 3
    assert paragraphs[0].startswith("На ходатайство о заочном аресте")
    assert all(paragraphs)


def test_parser_rejects_article_without_title() -> None:
    raw_document = RawDocument(
        external_id="test-article",
        url="https://example.test/article",
        fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
        content_type="text/html",
        content=b"""
            <html>
                <body>
                    <div class="field--name-field-express-text">
                        <p>Article text</p>
                    </div>
                </body>
            </html>
        """,
    )

    parser = OvdInfoArticleParser()

    with pytest.raises(ParseError, match="Title not found"):
        parser.parse(raw_document)


def test_parser_rejects_article_without_paragraphs() -> None:
    raw_document = RawDocument(
        external_id="test-article",
        url="https://example.test/article",
        fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
        content_type="text/html",
        content=b"""
            <html>
                <body>
                    <h1 class="express-text-heading">Article title</h1>
                </body>
            </html>
        """,
    )

    parser = OvdInfoArticleParser()

    with pytest.raises(ParseError, match="Paragraphs not found"):
        parser.parse(raw_document)


def test_parser_reads_digest_articles_written_as_list_items() -> None:
    """Real case: OVD-Info daily digests (e.g. /express-news/2026/05/25/novoe-delo-...)
    put every story into `<ul><li>` instead of `<p>`; they were rejected as
    "Paragraphs not found" and never reached extraction."""
    html = """
    <html><body>
      <h1 class="express-text-heading">Новое дело, пикет у Кремля и условный срок</h1>
      <div id="article_published">25.05.2026, 19:40</div>
      <div class="field field--name-field-express-text">
        <p>Главное за день.</p>
        <ul>
          <li>Против Анастасии Чумаковой <a href="https://example.test">возбудили</a> дело.</li>
          <li>На активистку Марину Халандач составили протокол.</li>
        </ul>
        <p>Подписывайтесь на рассылку.</p>
      </div>
    </body></html>
    """.encode()
    raw_document = RawDocument(
        external_id="digest",
        url="https://example.test/digest",
        fetched_at=datetime(2026, 5, 25, tzinfo=UTC),
        content_type="text/html",
        content=html,
    )

    article = OvdInfoArticleParser().parse(raw_document)

    assert article.text.split("\n\n") == [
        "Главное за день.",
        "Против Анастасии Чумаковой возбудили дело.",
        "На активистку Марину Халандач составили протокол.",
        "Подписывайтесь на рассылку.",
    ]

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from sources.ingestion_errors import ParseError
from sources.models import RawDocument
from sources.sota_vision.article_parser import SotaVisionArticleParser

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "sota_vision_article.html"


def test_parser_extracts_article() -> None:
    html_bytes = FIXTURE_PATH.read_bytes()
    assert html_bytes

    raw_document = RawDocument(
        external_id="/rossiya-zakryvaet-genkonsulstvo-germanii-v-sankt-peterburge/",
        url="https://sota.vision/rossiya-zakryvaet-genkonsulstvo-germanii-v-sankt-peterburge/",
        fetched_at=datetime(2026, 9, 13, tzinfo=UTC),
        content_type="text/html; charset=UTF-8",
        content=html_bytes,
    )
    parser = SotaVisionArticleParser()
    article = parser.parse(raw_document)
    paragraphs = article.text.split("\n\n")

    assert article.external_id == raw_document.external_id
    assert article.url == raw_document.url

    assert article.title == "Россия закрывает генконсульство Германии в Санкт-Петербурге"
    assert article.published_at == datetime(
        2026,
        9,
        7,
        tzinfo=ZoneInfo("Europe/Moscow"),
    )

    assert len(paragraphs) == 3
    assert paragraphs[0].startswith("МИД России объявил о прекращении работы")
    assert all(paragraphs)


def test_parser_rejects_article_without_title() -> None:
    raw_document = RawDocument(
        external_id="test-article",
        url="https://sota.vision/test-article/",
        fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
        content_type="text/html",
        content=b"""
            <html>
                <body>
                    <div class="entry-content">
                        <p>Article text</p>
                    </div>
                </body>
            </html>
        """,
    )

    parser = SotaVisionArticleParser()

    with pytest.raises(ParseError, match="Title not found"):
        parser.parse(raw_document)


def test_parser_rejects_article_without_paragraphs() -> None:
    raw_document = RawDocument(
        external_id="test-article",
        url="https://sota.vision/test-article/",
        fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
        content_type="text/html",
        content=b"""
            <html>
                <body>
                    <h1 class="entry-title">Article title</h1>
                </body>
            </html>
        """,
    )

    parser = SotaVisionArticleParser()

    with pytest.raises(ParseError, match="Paragraphs not found"):
        parser.parse(raw_document)


def test_parser_allows_missing_published_date() -> None:
    raw_document = RawDocument(
        external_id="test-article",
        url="https://sota.vision/test-article/",
        fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
        content_type="text/html",
        content=b"""
            <html>
                <body>
                    <h1 class="entry-title">Article title</h1>
                    <div class="entry-content">
                        <p>Article text</p>
                    </div>
                </body>
            </html>
        """,
    )

    parser = SotaVisionArticleParser()
    article = parser.parse(raw_document)

    assert article.published_at is None

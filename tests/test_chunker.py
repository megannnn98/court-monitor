from datetime import UTC, datetime

from chunker import Chunker
from models import ParsedArticle


def test_chunker() -> None:
    text = """  Первый

                Второй



                Третий"""
    article = ParsedArticle(
        external_id="test-article",
        url="https://example.test/article",
        title="На комика Сашу Долгополова завели дело о реабилитации нацизма",
        published_at=datetime(2026, 8, 13, tzinfo=UTC),
        text=text,
    )
    chunks = Chunker().split(article)

    assert len(chunks) == 3
    assert [chunk.ordinal for chunk in chunks] == [0, 1, 2]
    assert [chunk.text for chunk in chunks] == [
        "Первый",
        "Второй",
        "Третий",
    ]

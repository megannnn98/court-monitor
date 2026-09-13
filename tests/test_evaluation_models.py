import pytest
from pydantic import ValidationError

from evaluation_models import (
    ArticleReference,
    EvaluationCase,
    EvaluationDocument,
)


def test_evaluation_case_strips_strings() -> None:
    case = EvaluationCase(
        query_id="  rehabilitation-of-nazism  ",
        query_text="  реабилитация нацизма  ",
        expected_article=ArticleReference(
            source_base_url="  https://ovd.info  ",
            external_id="  dolgopolov-article  ",
        ),
    )

    assert case.query_id == "rehabilitation-of-nazism"
    assert case.query_text == "реабилитация нацизма"
    assert case.expected_article.source_base_url == "https://ovd.info"
    assert case.expected_article.external_id == "dolgopolov-article"


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_empty_article_reference_strings_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        ArticleReference(
            source_base_url=value,
            external_id="article-1",
        )

    with pytest.raises(ValidationError):
        ArticleReference(
            source_base_url="https://ovd.info",
            external_id=value,
        )


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_empty_evaluation_case_strings_are_rejected(value: str) -> None:
    expected_article = ArticleReference(
        source_base_url="https://ovd.info",
        external_id="article-1",
    )

    with pytest.raises(ValidationError):
        EvaluationCase(
            query_id=value,
            query_text="реабилитация нацизма",
            expected_article=expected_article,
        )

    with pytest.raises(ValidationError):
        EvaluationCase(
            query_id="rehabilitation-of-nazism",
            query_text=value,
            expected_article=expected_article,
        )


def test_evaluation_document_strips_strings() -> None:
    document = EvaluationDocument(
        source_base_url="  https://ovd.info  ",
        external_id="  article-1  ",
        canonical_url="  https://ovd.info/article-1  ",
        title="  Дело о реабилитации нацизма  ",
        text="  Суд рассмотрел уголовное дело.\n\nОбвинение связано с реабилитацией нацизма.  ",
    )

    assert document.source_base_url == "https://ovd.info"
    assert document.external_id == "article-1"
    assert document.canonical_url == "https://ovd.info/article-1"
    assert document.title == "Дело о реабилитации нацизма"
    assert (
        document.text
        == "Суд рассмотрел уголовное дело.\n\nОбвинение связано с реабилитацией нацизма."
    )


@pytest.mark.parametrize("text", ["", "   ", "\t\n"])
def test_evaluation_document_rejects_empty_text(text: str) -> None:
    with pytest.raises(ValidationError):
        EvaluationDocument(
            source_base_url="https://ovd.info",
            external_id="article-1",
            canonical_url="https://ovd.info/article-1",
            title="Тестовая статья",
            text=text,
        )

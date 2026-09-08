import pytest
from pydantic import ValidationError

from evaluation_models import (
    ChunkReference,
    EvaluationCase,
    EvaluationDocument,
)


def test_evaluation_case_strips_strings() -> None:
    case = EvaluationCase(
        query_id="  rehabilitation-of-nazism  ",
        query_text="  реабилитация нацизма  ",
        expected_chunk=ChunkReference(
            source_base_url="  https://ovd.info  ",
            external_id="  dolgopolov-article  ",
            ordinal=1,
        ),
    )

    assert case.query_id == "rehabilitation-of-nazism"
    assert case.query_text == "реабилитация нацизма"
    assert case.expected_chunk.source_base_url == "https://ovd.info"
    assert case.expected_chunk.external_id == "dolgopolov-article"
    assert case.expected_chunk.ordinal == 1


def test_negative_chunk_ordinal_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ChunkReference(
            source_base_url="https://ovd.info",
            external_id="dolgopolov-article",
            ordinal=-1,
        )


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_empty_chunk_reference_strings_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        ChunkReference(
            source_base_url=value,
            external_id="article-1",
            ordinal=0,
        )

    with pytest.raises(ValidationError):
        ChunkReference(
            source_base_url="https://ovd.info",
            external_id=value,
            ordinal=0,
        )


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_empty_evaluation_case_strings_are_rejected(value: str) -> None:
    expected_chunk = ChunkReference(
        source_base_url="https://ovd.info",
        external_id="article-1",
        ordinal=0,
    )

    with pytest.raises(ValidationError):
        EvaluationCase(
            query_id=value,
            query_text="реабилитация нацизма",
            expected_chunk=expected_chunk,
        )

    with pytest.raises(ValidationError):
        EvaluationCase(
            query_id="rehabilitation-of-nazism",
            query_text=value,
            expected_chunk=expected_chunk,
        )


def test_evaluation_document_strips_strings() -> None:
    document = EvaluationDocument(
        source_base_url="  https://ovd.info  ",
        external_id="  article-1  ",
        canonical_url="  https://ovd.info/article-1  ",
        title="  Дело о реабилитации нацизма  ",
        chunks=[
            "  Суд рассмотрел уголовное дело.  ",
            "  Обвинение связано с реабилитацией нацизма.  ",
        ],
    )

    assert document.source_base_url == "https://ovd.info"
    assert document.external_id == "article-1"
    assert document.canonical_url == "https://ovd.info/article-1"
    assert document.title == "Дело о реабилитации нацизма"
    assert document.chunks == [
        "Суд рассмотрел уголовное дело.",
        "Обвинение связано с реабилитацией нацизма.",
    ]


@pytest.mark.parametrize("chunk", ["", "   ", "\t\n"])
def test_evaluation_document_rejects_empty_chunk(chunk: str) -> None:
    with pytest.raises(ValidationError):
        EvaluationDocument(
            source_base_url="https://ovd.info",
            external_id="article-1",
            canonical_url="https://ovd.info/article-1",
            title="Тестовая статья",
            chunks=[chunk],
        )

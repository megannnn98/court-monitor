from unittest.mock import Mock

import pytest

from evaluation_models import ChunkReference, EvaluationCase
from models import SearchHit, SearchQuery
from search_backend import SearchBackend
from search_evaluator import SearchEvaluator


def make_hit(
    *,
    external_id: str,
    ordinal: int,
    chunk_id: int,
) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        article_id=chunk_id,
        source_base_url="https://ovd.info",
        external_id=external_id,
        ordinal=ordinal,
        title="Тестовая статья",
        published_at=None,
        url=f"https://ovd.info/{external_id}",
        text="Текст чанка",
        score=1.0,
    )


def test_evaluate_case_returns_reciprocal_rank() -> None:
    search = Mock(spec=SearchBackend)
    search.search.return_value = [
        make_hit(
            external_id="weather-report",
            ordinal=0,
            chunk_id=1,
        ),
        make_hit(
            external_id="rehabilitation-nazism",
            ordinal=1,
            chunk_id=2,
        ),
    ]

    case = EvaluationCase(
        query_id="rehabilitation-of-nazism",
        query_text="реабилитация нацизма",
        expected_chunk=ChunkReference(
            source_base_url="https://ovd.info",
            external_id="rehabilitation-nazism",
            ordinal=1,
        ),
    )

    evaluator = SearchEvaluator(search=search, limit=3)

    result = evaluator.evaluate_case(case)

    assert result.query_id == "rehabilitation-of-nazism"
    assert result.query_text == "реабилитация нацизма"
    assert result.expected_chunk == case.expected_chunk
    assert result.reciprocal_rank == pytest.approx(0.5)

    assert result.retrieved_chunks == [
        ChunkReference(
            source_base_url="https://ovd.info",
            external_id="weather-report",
            ordinal=0,
        ),
        ChunkReference(
            source_base_url="https://ovd.info",
            external_id="rehabilitation-nazism",
            ordinal=1,
        ),
    ]
    search.search.assert_called_once_with(
        SearchQuery(
            text="реабилитация нацизма",
            limit=3,
        )
    )


def test_evaluate_returns_mean_reciprocal_rank() -> None:
    search = Mock(spec=SearchBackend)

    correct_first = make_hit(
        external_id="rehabilitation-nazism",
        ordinal=1,
        chunk_id=1,
    )
    irrelevant = make_hit(
        external_id="weather-report",
        ordinal=0,
        chunk_id=2,
    )

    search.search.side_effect = [
        [correct_first],
        [irrelevant],
    ]

    cases = [
        EvaluationCase(
            query_id="rehabilitation-of-nazism",
            query_text="реабилитация нацизма",
            expected_chunk=ChunkReference(
                source_base_url="https://ovd.info",
                external_id="rehabilitation-nazism",
                ordinal=1,
            ),
        ),
        EvaluationCase(
            query_id="military-fakes",
            query_text="фейки об армии",
            expected_chunk=ChunkReference(
                source_base_url="https://ovd.info",
                external_id="military-fakes",
                ordinal=0,
            ),
        ),
    ]

    evaluator = SearchEvaluator(search=search, limit=3)

    report = evaluator.evaluate(cases)

    assert report.mean_reciprocal_rank == pytest.approx(0.5)
    assert len(report.results) == 2

    assert report.results[0].query_id == "rehabilitation-of-nazism"
    assert report.results[0].reciprocal_rank == pytest.approx(1.0)

    assert report.results[1].query_id == "military-fakes"
    assert report.results[1].reciprocal_rank == pytest.approx(0.0)

    assert search.search.call_count == 2

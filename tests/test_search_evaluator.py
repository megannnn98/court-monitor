from unittest.mock import Mock

import pytest

from evaluation_models import ArticleReference, EvaluationCase
from models import SearchHit, SearchQuery
from search_backend import SearchBackend
from search_evaluator import SearchEvaluator


def make_hit(
    *,
    external_id: str,
    article_id: int,
) -> SearchHit:
    return SearchHit(
        article_id=article_id,
        source_base_url="https://ovd.info",
        external_id=external_id,
        title="Тестовая статья",
        published_at=None,
        url=f"https://ovd.info/{external_id}",
        text="Текст статьи",
        score=1.0,
    )


def test_evaluate_case_returns_reciprocal_rank() -> None:
    search = Mock(spec=SearchBackend)
    search.search.return_value = [
        make_hit(
            external_id="weather-report",
            article_id=1,
        ),
        make_hit(
            external_id="rehabilitation-nazism",
            article_id=2,
        ),
    ]

    case = EvaluationCase(
        query_id="rehabilitation-of-nazism",
        query_text="реабилитация нацизма",
        expected_article=ArticleReference(
            source_base_url="https://ovd.info",
            external_id="rehabilitation-nazism",
        ),
    )

    evaluator = SearchEvaluator(search=search, limit=3)

    result = evaluator.evaluate_case(case)

    assert result.query_id == "rehabilitation-of-nazism"
    assert result.query_text == "реабилитация нацизма"
    assert result.expected_article == case.expected_article
    assert result.reciprocal_rank == pytest.approx(0.5)

    assert result.retrieved_articles == [
        ArticleReference(
            source_base_url="https://ovd.info",
            external_id="weather-report",
        ),
        ArticleReference(
            source_base_url="https://ovd.info",
            external_id="rehabilitation-nazism",
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
        article_id=1,
    )
    irrelevant = make_hit(
        external_id="weather-report",
        article_id=2,
    )

    search.search.side_effect = [
        [correct_first],
        [irrelevant],
    ]

    cases = [
        EvaluationCase(
            query_id="rehabilitation-of-nazism",
            query_text="реабилитация нацизма",
            expected_article=ArticleReference(
                source_base_url="https://ovd.info",
                external_id="rehabilitation-nazism",
            ),
        ),
        EvaluationCase(
            query_id="military-fakes",
            query_text="фейки об армии",
            expected_article=ArticleReference(
                source_base_url="https://ovd.info",
                external_id="military-fakes",
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

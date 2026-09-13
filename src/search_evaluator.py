from collections.abc import Sequence

from evaluation_metrics import (
    mean_reciprocal_rank as calculate_mean_reciprocal_rank,
)
from evaluation_metrics import reciprocal_rank
from evaluation_models import (
    ArticleReference,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationReport,
)
from models import SearchQuery
from search_backend import SearchBackend


class SearchEvaluator:
    def __init__(
        self,
        search: SearchBackend,
        limit: int = 3,
    ) -> None:
        self._search = search
        self._limit = limit

    def evaluate_case(
        self,
        case: EvaluationCase,
    ) -> EvaluationCaseResult:
        hits = self._search.search(
            SearchQuery(
                text=case.query_text,
                limit=self._limit,
            )
        )

        retrieved_articles = [
            ArticleReference(
                source_base_url=hit.source_base_url,
                external_id=hit.external_id,
            )
            for hit in hits
        ]

        score = reciprocal_rank(
            retrieved=retrieved_articles,
            expected_article=case.expected_article,
        )

        return EvaluationCaseResult(
            query_id=case.query_id,
            query_text=case.query_text,
            expected_article=case.expected_article,
            retrieved_articles=retrieved_articles,
            reciprocal_rank=score,
        )

    def evaluate(
        self,
        cases: Sequence[EvaluationCase],
    ) -> EvaluationReport:
        results = [self.evaluate_case(case) for case in cases]

        mean_score = calculate_mean_reciprocal_rank([result.reciprocal_rank for result in results])

        return EvaluationReport(
            results=results,
            mean_reciprocal_rank=mean_score,
        )

from collections.abc import Sequence

from evaluation_metrics import (
    mean_reciprocal_rank as calculate_mean_reciprocal_rank,
)
from evaluation_metrics import reciprocal_rank
from evaluation_models import (
    ChunkReference,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationReport,
)
from models import SearchQuery
from postgres_lexical_search import PostgresLexicalSearch


class SearchEvaluator:
    def __init__(
        self,
        search: PostgresLexicalSearch,
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

        retrieved_chunks = [
            ChunkReference(
                source_base_url=hit.source_base_url,
                external_id=hit.external_id,
                ordinal=hit.ordinal,
            )
            for hit in hits
        ]

        score = reciprocal_rank(
            retrieved=retrieved_chunks,
            expected_chunk=case.expected_chunk,
        )

        return EvaluationCaseResult(
            query_id=case.query_id,
            query_text=case.query_text,
            expected_chunk=case.expected_chunk,
            retrieved_chunks=retrieved_chunks,
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

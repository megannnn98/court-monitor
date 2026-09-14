from dataclasses import dataclass
from typing import Protocol

from sources.ingestion_errors import IngestionError
from sources.models import IngestionResult, SourceReference
from sources.source_adapter import SourceAdapter


class ArticleIngestionPipeline(Protocol):
    async def run(
        self,
        reference: SourceReference,
    ) -> IngestionResult: ...


@dataclass(frozen=True)
class SourceIngestionFailure:
    reference: SourceReference
    error: IngestionError


@dataclass(frozen=True)
class SourceIngestionResult:
    results: list[IngestionResult]
    failures: list[SourceIngestionFailure]


class SourceIngestion:
    def __init__(
        self,
        source_adapter: SourceAdapter,
        pipeline: ArticleIngestionPipeline,
    ) -> None:
        self._source_adapter = source_adapter
        self._pipeline = pipeline

    async def run(
        self,
        *,
        limit: int,
    ) -> SourceIngestionResult:
        references = await self._source_adapter.discover(limit=limit)

        results: list[IngestionResult] = []
        failures: list[SourceIngestionFailure] = []

        for reference in references:
            try:
                result = await self._pipeline.run(reference)
            except IngestionError as exc:
                failures.append(
                    SourceIngestionFailure(
                        reference=reference,
                        error=exc,
                    )
                )
                continue

            results.append(result)

        return SourceIngestionResult(
            results=results,
            failures=failures,
        )

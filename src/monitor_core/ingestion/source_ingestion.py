from dataclasses import dataclass
from typing import Protocol

from monitor_core.errors import IngestionError
from monitor_core.model.document import IngestionResult
from monitor_core.model.source import SourceReference
from monitor_core.ports.discovery import SourceAdapter


class ArticleIngestionPipeline[R](Protocol):
    async def run(
        self,
        reference: SourceReference,
    ) -> IngestionResult[R]: ...


@dataclass(frozen=True)
class SourceIngestionFailure:
    reference: SourceReference
    error: IngestionError


@dataclass(frozen=True)
class SourceIngestionResult[R]:
    results: list[IngestionResult[R]]
    failures: list[SourceIngestionFailure]


class SourceIngestion[R]:
    def __init__(
        self,
        source_adapter: SourceAdapter,
        pipeline: ArticleIngestionPipeline[R],
    ) -> None:
        self._source_adapter = source_adapter
        self._pipeline = pipeline

    async def run(
        self,
        *,
        limit: int,
    ) -> SourceIngestionResult[R]:
        references = await self._source_adapter.discover(limit=limit)

        results: list[IngestionResult[R]] = []
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

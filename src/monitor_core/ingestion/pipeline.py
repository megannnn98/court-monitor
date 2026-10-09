from monitor_core.model.document import IngestionResult
from monitor_core.model.source import SourceReference
from monitor_core.ports.fetcher import DocumentFetcher
from monitor_core.ports.parser import ArticleParser
from monitor_core.ports.persistence import IngestionPersistence


class IngestionPipeline:
    def __init__(
        self,
        source_adapter: DocumentFetcher,
        parser: ArticleParser,
        persistence: IngestionPersistence,
    ) -> None:
        self._source_adapter = source_adapter
        self._parser = parser
        self._persistence = persistence

    async def run(self, reference: SourceReference) -> IngestionResult:
        raw_document = await self._source_adapter.fetch(reference)
        parsed = self._parser.parse(raw_document)
        persistence_result = self._persistence.save(
            raw_document,
            parsed,
        )

        return IngestionResult(
            article=parsed,
            persistence=persistence_result,
        )

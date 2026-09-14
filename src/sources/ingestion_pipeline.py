from sources.article_parser import ArticleParser
from sources.models import IngestionResult, SourceReference
from sources.persistence import IngestionPersistence
from sources.source_adapter import DocumentFetcher


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

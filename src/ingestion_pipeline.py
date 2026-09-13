from article_parser import OvdInfoArticleParser
from models import IngestionResult, SourceReference
from persistence import IngestionPersistence
from website_adapter import WebsiteAdapter


class IngestionPipeline:
    def __init__(
        self,
        website_adapter: WebsiteAdapter,
        parser: OvdInfoArticleParser,
        persistence: IngestionPersistence,
    ) -> None:
        self._website_adapter = website_adapter
        self._parser = parser
        self._persistence = persistence

    async def run(self, reference: SourceReference) -> IngestionResult:
        raw_document = await self._website_adapter.fetch(reference)
        parsed = self._parser.parse(raw_document)
        persistence_result = self._persistence.save(
            raw_document,
            parsed,
        )

        return IngestionResult(
            article=parsed,
            persistence=persistence_result,
        )

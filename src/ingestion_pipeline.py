from article_parser import OvdInfoArticleParser
from chunker import Chunker
from models import IngestionResult, SourceReference
from persistence import IngestionPersistence
from website_adapter import WebsiteAdapter


class IngestionPipeline:
    def __init__(
        self,
        website_adapter: WebsiteAdapter,
        parser: OvdInfoArticleParser,
        chunker: Chunker,
        persistence: IngestionPersistence,
    ) -> None:
        self._website_adapter = website_adapter
        self._parser = parser
        self._chunker = chunker
        self._persistence = persistence

    async def run(self, reference: SourceReference) -> IngestionResult:
        raw_document = await self._website_adapter.fetch(reference)
        parsed = self._parser.parse(raw_document)
        chunks = self._chunker.split(parsed)
        persistence_result = self._persistence.save(
            raw_document,
            parsed,
            chunks,
        )

        return IngestionResult(
            article=parsed,
            chunks=chunks,
            persistence=persistence_result,
        )

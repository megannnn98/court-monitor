from dataclasses import dataclass

from monitor_core.model.document import IngestionResult, ParsedArticle, RawDocument
from monitor_core.model.source import SourceReference
from monitor_core.ports.fetcher import DocumentFetcher
from monitor_core.ports.parser import ArticleParser
from monitor_core.ports.persistence import IngestionPersistence


@dataclass(frozen=True)
class FetchedArticle:
    """A document read from its source and parsed, not yet saved."""

    raw_document: RawDocument
    article: ParsedArticle


class IngestionPipeline[R]:
    """Fetch, parse and save one reference.

    `run` does all three. A caller that decides between parsing and saving (skips an
    article by its date, say) calls `read` and then `save` itself."""

    def __init__(
        self,
        source_adapter: DocumentFetcher,
        parser: ArticleParser,
        persistence: IngestionPersistence[R],
    ) -> None:
        self._source_adapter = source_adapter
        self._parser = parser
        self._persistence = persistence

    async def run(self, reference: SourceReference) -> IngestionResult[R]:
        return self.save(await self.read(reference))

    async def read(self, reference: SourceReference) -> FetchedArticle:
        raw_document = await self._source_adapter.fetch(reference)
        parsed = self._parser.parse(raw_document)
        return FetchedArticle(raw_document=raw_document, article=parsed)

    def save(self, fetched: FetchedArticle) -> IngestionResult[R]:
        persistence_result = self._persistence.save(
            fetched.raw_document,
            fetched.article,
        )

        return IngestionResult(
            article=fetched.article,
            persistence=persistence_result,
        )

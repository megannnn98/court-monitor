from typing import Protocol

from sources.models import ParsedArticle, PersistenceResult, RawDocument


class IngestionPersistence(Protocol):
    def save(
        self,
        raw_document: RawDocument,
        article: ParsedArticle,
    ) -> PersistenceResult: ...

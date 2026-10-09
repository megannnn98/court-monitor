from typing import Protocol

from monitor_core.model.document import ParsedArticle, PersistenceResult, RawDocument


class IngestionPersistence(Protocol):
    def save(
        self,
        raw_document: RawDocument,
        article: ParsedArticle,
    ) -> PersistenceResult: ...

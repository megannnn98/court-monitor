from typing import Protocol

from monitor_core.model.document import ParsedArticle, RawDocument


class IngestionPersistence[R](Protocol):
    """Stores a document and its article; what it hands back (`R`) is its own business."""

    def save(
        self,
        raw_document: RawDocument,
        article: ParsedArticle,
    ) -> R: ...

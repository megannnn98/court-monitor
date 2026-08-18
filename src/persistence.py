from collections.abc import Sequence
from typing import Protocol

from models import ArticleChunk, ParsedArticle, PersistenceResult, RawDocument


class IngestionPersistence(Protocol):
    def save(
        self,
        raw_document: RawDocument,
        article: ParsedArticle,
        chunks: Sequence[ArticleChunk],
    ) -> PersistenceResult: ...

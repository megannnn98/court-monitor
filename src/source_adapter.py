from typing import Protocol

from models import RawDocument, SourceReference


class DocumentFetcher(Protocol):
    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument: ...


class SourceAdapter(DocumentFetcher, Protocol):
    async def discover(
        self,
        *,
        limit: int,
    ) -> list[SourceReference]: ...

from typing import Protocol

from sources.models import RawDocument, SourceReference


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

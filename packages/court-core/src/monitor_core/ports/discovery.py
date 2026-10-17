from typing import Protocol

from monitor_core.model.source import SourceReference
from monitor_core.ports.fetcher import DocumentFetcher


class SourceAdapter(DocumentFetcher, Protocol):
    async def discover(
        self,
        *,
        limit: int,
    ) -> list[SourceReference]: ...

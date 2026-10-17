from typing import Protocol

from monitor_core.model.document import RawDocument
from monitor_core.model.source import SourceReference


class DocumentFetcher(Protocol):
    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument: ...

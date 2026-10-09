import asyncio

from monitor_core.errors import TransientFetchError
from monitor_core.model.document import RawDocument
from monitor_core.model.source import SourceReference
from monitor_core.ports.fetcher import DocumentFetcher
from monitor_core.retry import retry_async


class RetryingDocumentFetcher:
    def __init__(
        self,
        fetcher: DocumentFetcher,
        *,
        max_attempts: int = 3,
        base_delay_seconds: float = 0.5,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero")

        if base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must not be negative")

        self._fetcher = fetcher
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds

    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument:
        return await retry_async(
            lambda: self._fetcher.fetch(reference),
            attempts=self._max_attempts,
            should_retry=lambda exc: isinstance(exc, TransientFetchError),
            delay_seconds=lambda failures: self._base_delay_seconds * (2 ** (failures - 1)),
            sleep=asyncio.sleep,
        )

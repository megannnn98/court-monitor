import asyncio

from sources.ingestion_errors import TransientFetchError
from sources.models import RawDocument, SourceReference
from sources.source_adapter import DocumentFetcher


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
        for attempt in range(self._max_attempts):
            try:
                return await self._fetcher.fetch(reference)
            except TransientFetchError:
                is_last_attempt = attempt == self._max_attempts - 1

                if is_last_attempt:
                    raise

                delay = self._base_delay_seconds * (2**attempt)
                await asyncio.sleep(delay)

        raise AssertionError("unreachable")

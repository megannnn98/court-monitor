import asyncio
import random
from collections.abc import Callable

import httpx

from monitor_core.errors import TransientFetchError
from monitor_core.model.document import RawDocument
from monitor_core.model.source import SourceReference
from monitor_core.ports.fetcher import DocumentFetcher


class RetryingDocumentFetcher:
    def __init__(
        self,
        fetcher: DocumentFetcher,
        *,
        max_attempts: int = 3,
        base_delay_seconds: float = 0.5,
        max_delay_seconds: float = 30.0,
        jitter_ratio: float = 0.0,
        uniform: Callable[[float, float], float] = random.uniform,
        on_retry: Callable[[SourceReference], None] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero")

        if base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must not be negative")
        if max_delay_seconds <= 0:
            raise ValueError("max_delay_seconds must be greater than zero")
        if not 0 <= jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between zero and one")

        self._fetcher = fetcher
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._jitter_ratio = jitter_ratio
        self._uniform = uniform
        # Told of each attempt that will be repeated, so a run can count its retries.
        self._on_retry = on_retry

    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument:
        failures = 0
        while True:
            try:
                return await self._fetcher.fetch(reference)
            except TransientFetchError as exc:
                failures += 1
                if failures >= self._max_attempts:
                    raise
                if self._on_retry is not None:
                    self._on_retry(reference)
                backoff = min(
                    self._max_delay_seconds,
                    self._base_delay_seconds * (2 ** (failures - 1)),
                )
                if self._jitter_ratio:
                    backoff = self._uniform(
                        backoff * (1 - self._jitter_ratio), backoff * (1 + self._jitter_ratio)
                    )
                await asyncio.sleep(max(backoff, exc.retry_after_seconds or 0.0))

    def bind_http_client(self, client: httpx.AsyncClient) -> None:
        """Forward the pooled source client to a client-aware wrapped fetcher."""
        binder = getattr(self._fetcher, "bind_http_client", None)
        if callable(binder):
            binder(client)

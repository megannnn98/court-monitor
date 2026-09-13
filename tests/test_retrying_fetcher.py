import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from ingestion_errors import PermanentFetchError, TransientFetchError
from models import RawDocument, SourceReference
from retrying_fetcher import RetryingDocumentFetcher

REFERENCE = SourceReference(
    external_id="/express-news/test",
    url="https://ovd.info/express-news/test",
)


def _raw_document() -> RawDocument:
    from datetime import UTC, datetime

    return RawDocument(
        external_id=REFERENCE.external_id,
        url=REFERENCE.url,
        fetched_at=datetime.now(tz=UTC),
        content_type="text/html",
        content=b"<html></html>",
    )


class FakeFetcher:
    def __init__(
        self,
        outcomes: list[RawDocument | Exception],
    ) -> None:
        self._outcomes = outcomes
        self.calls = 0

    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument:
        self.calls += 1
        outcome = self._outcomes[self.calls - 1]

        if isinstance(outcome, Exception):
            raise outcome

        return outcome


def test_fetch_returns_on_first_attempt() -> None:
    async def run() -> None:
        expected = _raw_document()
        fetcher = FakeFetcher([expected])

        retrying_fetcher = RetryingDocumentFetcher(
            fetcher,
            max_attempts=3,
            base_delay_seconds=0,
        )

        result = await retrying_fetcher.fetch(REFERENCE)

        assert result == expected
        assert fetcher.calls == 1

    asyncio.run(run())


def test_fetch_retries_transient_error_until_success() -> None:
    async def run() -> None:
        expected = _raw_document()

        fetcher = FakeFetcher(
            [
                TransientFetchError("temporary"),
                TransientFetchError("temporary"),
                expected,
            ]
        )

        retrying_fetcher = RetryingDocumentFetcher(
            fetcher,
            max_attempts=3,
            base_delay_seconds=0,
        )

        result = await retrying_fetcher.fetch(REFERENCE)

        assert result == expected
        assert fetcher.calls == 3

    asyncio.run(run())


def test_fetch_raises_after_max_attempts() -> None:
    async def run() -> None:
        fetcher = FakeFetcher(
            [
                TransientFetchError("first"),
                TransientFetchError("second"),
                TransientFetchError("third"),
            ]
        )

        retrying_fetcher = RetryingDocumentFetcher(
            fetcher,
            max_attempts=3,
            base_delay_seconds=0,
        )

        with pytest.raises(
            TransientFetchError,
            match="third",
        ):
            await retrying_fetcher.fetch(REFERENCE)

        assert fetcher.calls == 3

    asyncio.run(run())


def test_fetch_does_not_retry_permanent_error() -> None:
    async def run() -> None:
        fetcher = FakeFetcher(
            [
                PermanentFetchError("not found"),
            ]
        )

        retrying_fetcher = RetryingDocumentFetcher(
            fetcher,
            max_attempts=3,
            base_delay_seconds=0,
        )

        with pytest.raises(
            PermanentFetchError,
            match="not found",
        ):
            await retrying_fetcher.fetch(REFERENCE)

        assert fetcher.calls == 1

    asyncio.run(run())


def test_fetch_uses_exponential_backoff() -> None:
    async def run() -> None:
        expected = _raw_document()

        fetcher = FakeFetcher(
            [
                TransientFetchError("first"),
                TransientFetchError("second"),
                expected,
            ]
        )

        retrying_fetcher = RetryingDocumentFetcher(
            fetcher,
            max_attempts=3,
            base_delay_seconds=0.5,
        )

        with patch(
            "retrying_fetcher.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep:
            result = await retrying_fetcher.fetch(REFERENCE)

        assert result == expected
        assert sleep.await_count == 2
        sleep.assert_any_await(0.5)
        sleep.assert_any_await(1.0)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("max_attempts", "base_delay_seconds"),
    [
        (0, 0.5),
        (-1, 0.5),
    ],
)
def test_rejects_invalid_max_attempts(
    max_attempts: int,
    base_delay_seconds: float,
) -> None:
    fetcher = FakeFetcher([])

    with pytest.raises(
        ValueError,
        match="max_attempts",
    ):
        RetryingDocumentFetcher(
            fetcher,
            max_attempts=max_attempts,
            base_delay_seconds=base_delay_seconds,
        )


def test_rejects_negative_base_delay() -> None:
    fetcher = FakeFetcher([])

    with pytest.raises(
        ValueError,
        match="base_delay_seconds",
    ):
        RetryingDocumentFetcher(
            fetcher,
            base_delay_seconds=-0.1,
        )

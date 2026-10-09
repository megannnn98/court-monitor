"""Characterization of `RetryingDocumentFetcher`'s retry loop.

`max_attempts` counts calls (1 means no retry; 0 is refused when built). Only a
`TransientFetchError` is retried, after base x 1, x 2, x 4 ... seconds through
`asyncio.sleep`, between calls only. The last error, or any other one, comes out as
the same object. The wait happens while the failure is being handled, so whatever
interrupts it carries that failure as its context.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from monitor_core.errors import PermanentFetchError, TransientFetchError
from monitor_core.ingestion import RetryingDocumentFetcher
from monitor_core.model import RawDocument, SourceReference

REFERENCE = SourceReference(external_id="a", url="https://example.test/a")
DOCUMENT = RawDocument(
    external_id="a",
    url="https://example.test/a",
    fetched_at=datetime(2026, 9, 1, tzinfo=UTC),
    content_type="text/html",
    content=b"<html></html>",
)


class Scripted:
    def __init__(self, outcomes: list[RawDocument | Exception]) -> None:
        self._outcomes = outcomes
        self.calls = 0

    async def fetch(self, reference: SourceReference) -> RawDocument:
        outcome = self._outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    async def record(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record)
    return sleeps


def _fetch(
    outcomes: list[RawDocument | Exception], *, max_attempts: int = 3, base: float = 0.5
) -> tuple[RawDocument, Scripted]:
    inner = Scripted(outcomes)
    fetcher = RetryingDocumentFetcher(inner, max_attempts=max_attempts, base_delay_seconds=base)
    return asyncio.run(fetcher.fetch(REFERENCE)), inner


def _failure(
    outcomes: list[RawDocument | Exception], *, max_attempts: int = 3, base: float = 0.5
) -> tuple[BaseException, Scripted]:
    inner = Scripted(outcomes)
    fetcher = RetryingDocumentFetcher(inner, max_attempts=max_attempts, base_delay_seconds=base)
    with pytest.raises(BaseException) as caught:
        asyncio.run(fetcher.fetch(REFERENCE))
    return caught.value, inner


def test_a_first_success_is_one_call_without_a_wait(slept: list[float]) -> None:
    document, inner = _fetch([DOCUMENT])

    assert document is DOCUMENT
    assert (inner.calls, slept) == (1, [])


def test_success_after_transient_failures(slept: list[float]) -> None:
    document, inner = _fetch([TransientFetchError("1"), TransientFetchError("2"), DOCUMENT])

    assert document is DOCUMENT
    assert (inner.calls, slept) == (3, [0.5, 1.0])


@pytest.mark.parametrize(
    ("max_attempts", "base", "sleeps"),
    [
        (1, 0.5, []),
        (2, 0.5, [0.5]),
        (3, 0.5, [0.5, 1.0]),
        (5, 0.3, [0.3, 0.6, 1.2, 2.4]),
        (3, 0.0, [0.0, 0.0]),
    ],
)
def test_max_attempts_counts_calls_and_the_last_error_comes_out_as_it_was(
    max_attempts: int, base: float, sleeps: list[float], slept: list[float]
) -> None:
    errors: list[RawDocument | Exception] = [
        TransientFetchError(str(n)) for n in range(max_attempts + 1)
    ]

    error, inner = _failure(errors, max_attempts=max_attempts, base=base)

    assert error is errors[max_attempts - 1]
    assert error.__cause__ is None and error.__context__ is None
    assert error.__suppress_context__ is False
    assert inner.calls == max_attempts
    assert slept == sleeps


@pytest.mark.parametrize(
    "final", [PermanentFetchError("404"), RuntimeError("bug"), ValueError("x")]
)
def test_any_other_error_comes_out_at_once(final: Exception, slept: list[float]) -> None:
    error, inner = _failure([final, DOCUMENT])

    assert error is final
    assert (inner.calls, slept) == (1, [])


def test_a_permanent_error_after_a_transient_one_stops_the_retries(slept: list[float]) -> None:
    permanent = PermanentFetchError("404")

    error, inner = _failure([TransientFetchError("503"), permanent, DOCUMENT])

    assert error is permanent
    assert (inner.calls, slept) == (2, [0.5])


def test_an_interrupted_wait_carries_the_failure_it_waited_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Interrupted(BaseException):
        pass

    async def interrupt(delay: float) -> None:
        raise Interrupted

    monkeypatch.setattr(asyncio, "sleep", interrupt)
    transient = TransientFetchError("503")

    error, inner = _failure([transient, DOCUMENT])

    assert isinstance(error, Interrupted)
    assert error.__context__ is transient
    assert inner.calls == 1


@pytest.mark.parametrize(("max_attempts", "base"), [(0, 0.5), (-1, 0.5), (3, -0.1)])
def test_a_bad_configuration_is_refused_when_built(max_attempts: int, base: float) -> None:
    with pytest.raises(ValueError):
        RetryingDocumentFetcher(Scripted([]), max_attempts=max_attempts, base_delay_seconds=base)

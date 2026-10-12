"""Calling an operation again after a failure.

What failure is worth another call, how many calls an operation gets and how long to
wait before each are the caller's: `retry` knows none of them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx

__all__ = ["retry", "retry_async", "transient_http_error"]

# Statuses worth asking again: too many requests, and the server's own failures.
_TRANSIENT_STATUS = 429
_SERVER_ERROR = 500


def transient_http_error(exc: Exception) -> bool:
    """A failure that may pass if the same request is sent again: a timeout, a dropped
    connection, HTTP 429 or 5xx. A 4xx other than 429 — a bad query, a refusal — is the
    request's own fault, and the same request fails the same way."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == _TRANSIENT_STATUS or status >= _SERVER_ERROR
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError))


def retry[T](
    operation: Callable[[], T],
    *,
    attempts: int,
    should_retry: Callable[[Exception], bool],
    delay_seconds: Callable[[int], float],
    sleep: Callable[[float], None],
) -> T:
    """`operation`'s result, calling it at most `attempts` times.

    After a failure that `should_retry` accepts, and if a call is left, waits
    `delay_seconds(failures)` — `failures` being how many calls have failed so far, from
    1 — and calls again. The last failure, or one `should_retry` rejects, is raised as
    it was: the same object, with its own traceback. An exception that interrupts a wait
    has the failure before it as its context."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    failures = 0
    while True:
        try:
            return operation()
        except Exception as exc:
            failures += 1
            if failures >= attempts or not should_retry(exc):
                raise
            # Waited while the failure is handled: what interrupts the wait carries it.
            sleep(delay_seconds(failures))


async def retry_async[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    should_retry: Callable[[Exception], bool],
    delay_seconds: Callable[[int], float],
    sleep: Callable[[float], Awaitable[None]],
) -> T:
    """`retry` for an awaited operation and an awaited wait: the same loop, the same terms."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    failures = 0
    while True:
        try:
            return await operation()
        except Exception as exc:
            failures += 1
            if failures >= attempts or not should_retry(exc):
                raise
            # Waited while the failure is handled: what interrupts the wait carries it.
            await sleep(delay_seconds(failures))

"""Calling an operation again after a failure.

What failure is worth another call, how many calls an operation gets and how long to
wait before each are the caller's: `retry` knows none of them.
"""

from __future__ import annotations

from collections.abc import Callable

__all__ = ["retry"]


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

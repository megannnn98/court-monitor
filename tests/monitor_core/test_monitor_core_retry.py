"""`retry` calls an operation again after a failure the caller calls retryable, waiting
what the caller says; the last failure, or one not retryable, comes out as it was."""

import asyncio

import pytest

from monitor_core.retry import retry, retry_async


class Flaky:
    def __init__(self, failures: list[Exception], result: str = "done") -> None:
        self._failures = failures
        self._result = result
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.calls <= len(self._failures):
            raise self._failures[self.calls - 1]
        return self._result


def _retry(operation: Flaky, *, attempts: int, sleeps: list[float]) -> str:
    return retry(
        operation,
        attempts=attempts,
        should_retry=lambda exc: isinstance(exc, TimeoutError),
        delay_seconds=lambda failures: failures * 10.0,
        sleep=sleeps.append,
    )


def test_a_first_success_waits_for_nothing() -> None:
    sleeps: list[float] = []
    operation = Flaky([])

    assert _retry(operation, attempts=3, sleeps=sleeps) == "done"
    assert (operation.calls, sleeps) == (1, [])


def test_retryable_failures_wait_by_how_many_failed_so_far() -> None:
    sleeps: list[float] = []
    operation = Flaky([TimeoutError(), TimeoutError()])

    assert _retry(operation, attempts=3, sleeps=sleeps) == "done"
    assert (operation.calls, sleeps) == (3, [10.0, 20.0])


def test_the_last_attempt_raises_its_own_error_without_a_wait() -> None:
    sleeps: list[float] = []
    errors: list[Exception] = [TimeoutError(1), TimeoutError(2), TimeoutError(3)]
    operation = Flaky(errors)

    with pytest.raises(TimeoutError) as caught:
        _retry(operation, attempts=3, sleeps=sleeps)

    assert caught.value is errors[2]
    assert caught.value.__context__ is None
    assert (operation.calls, sleeps) == (3, [10.0, 20.0])


def test_one_attempt_is_no_retry() -> None:
    sleeps: list[float] = []
    operation = Flaky([TimeoutError()])

    with pytest.raises(TimeoutError):
        _retry(operation, attempts=1, sleeps=sleeps)
    assert (operation.calls, sleeps) == (1, [])


def test_a_failure_not_retryable_comes_out_at_once() -> None:
    sleeps: list[float] = []
    final = ValueError("final")
    operation = Flaky([TimeoutError(), final])

    with pytest.raises(ValueError) as caught:
        _retry(operation, attempts=5, sleeps=sleeps)

    assert caught.value is final
    assert (operation.calls, sleeps) == (2, [10.0])


def test_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError, match="attempts"):
        _retry(Flaky([]), attempts=0, sleeps=[])


def test_an_interrupted_wait_carries_the_failure_it_waited_after() -> None:
    class Interrupted(BaseException):
        pass

    def interrupt(seconds: float) -> None:
        raise Interrupted

    failure = TimeoutError()
    operation = Flaky([failure])

    with pytest.raises(Interrupted) as caught:
        retry(
            operation,
            attempts=3,
            should_retry=lambda exc: True,
            delay_seconds=lambda failures: 1.0,
            sleep=interrupt,
        )

    assert caught.value.__context__ is failure
    assert operation.calls == 1


# `retry_async`: the same loop for an awaited operation and an awaited wait.


class AsyncFlaky:
    def __init__(self, failures: list[Exception], result: str = "done") -> None:
        self._failures = failures
        self._result = result
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.calls <= len(self._failures):
            raise self._failures[self.calls - 1]
        return self._result


def _retry_async(operation: AsyncFlaky, *, attempts: int, sleeps: list[float]) -> str:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return asyncio.run(
        retry_async(
            operation,
            attempts=attempts,
            should_retry=lambda exc: isinstance(exc, TimeoutError),
            delay_seconds=lambda failures: failures * 10.0,
            sleep=sleep,
        )
    )


def test_async_retryable_failures_wait_by_how_many_failed_so_far() -> None:
    sleeps: list[float] = []
    operation = AsyncFlaky([TimeoutError(), TimeoutError()])

    assert _retry_async(operation, attempts=3, sleeps=sleeps) == "done"
    assert (operation.calls, sleeps) == (3, [10.0, 20.0])


def test_async_last_attempt_raises_its_own_error_without_a_wait() -> None:
    sleeps: list[float] = []
    errors: list[Exception] = [TimeoutError(1), TimeoutError(2)]
    operation = AsyncFlaky(errors)

    with pytest.raises(TimeoutError) as caught:
        _retry_async(operation, attempts=2, sleeps=sleeps)

    assert caught.value is errors[1]
    assert caught.value.__context__ is None
    assert (operation.calls, sleeps) == (2, [10.0])


def test_async_failure_not_retryable_comes_out_at_once() -> None:
    sleeps: list[float] = []
    final = ValueError("final")
    operation = AsyncFlaky([final])

    with pytest.raises(ValueError) as caught:
        _retry_async(operation, attempts=5, sleeps=sleeps)

    assert caught.value is final
    assert (operation.calls, sleeps) == (1, [])


def test_async_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError, match="attempts"):
        _retry_async(AsyncFlaky([]), attempts=0, sleeps=[])


def test_async_interrupted_wait_carries_the_failure_it_waited_after() -> None:
    class Interrupted(BaseException):
        pass

    async def interrupt(seconds: float) -> None:
        raise Interrupted

    failure = TimeoutError()
    operation = AsyncFlaky([failure])

    with pytest.raises(Interrupted) as caught:
        asyncio.run(
            retry_async(
                operation,
                attempts=3,
                should_retry=lambda exc: True,
                delay_seconds=lambda failures: 1.0,
                sleep=interrupt,
            )
        )

    assert caught.value.__context__ is failure

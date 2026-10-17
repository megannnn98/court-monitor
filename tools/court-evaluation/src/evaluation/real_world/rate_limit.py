"""Polite HTTP access for corpus reconstruction.

At most one request per `min_interval_seconds` per domain, sequential. A 403
or 429 stops the source (never retried around), repeated 5xx/transport
failures first slow the source down, then stop it. Stopping is an
infrastructure status in the manifest, not a quality failure.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

STOP_STATUS_CODES = frozenset({403, 429})
SLOW_DOWN_AFTER_CONSECUTIVE_FAILURES = 3
STOP_AFTER_CONSECUTIVE_FAILURES = 6


class SourceStoppedError(RuntimeError):
    """The site asked us to stop (or keeps failing); no further requests are sent.

    Deliberately not an httpx or ingestion error, so no retry layer catches it.
    """

    def __init__(self, host: str, reason: str) -> None:
        super().__init__(f"{host}: stopped ({reason})")
        self.host = host
        self.reason = reason


class DomainRateLimiter:
    def __init__(
        self,
        min_interval_seconds: float = 1.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must not be negative")
        self._default_interval = min_interval_seconds
        self._intervals: dict[str, float] = {}
        self._last_request: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._clock = clock
        self._sleep = sleep

    def interval(self, host: str) -> float:
        return self._intervals.get(host, self._default_interval)

    def slow_down(self, host: str, factor: float = 2.0) -> None:
        self._intervals[host] = self.interval(host) * factor

    async def wait(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last_request.get(host)
            if last is not None:
                delay = self.interval(host) - (self._clock() - last)
                if delay > 0:
                    await self._sleep(delay)
            self._last_request[host] = self._clock()


@dataclass
class HostHealth:
    status_counts: Counter[str] = field(default_factory=Counter)
    consecutive_failures: int = 0
    stop_reason: str | None = None


class SourceHealth:
    def __init__(self, limiter: DomainRateLimiter) -> None:
        self._limiter = limiter
        self._hosts: dict[str, HostHealth] = {}

    def host(self, host: str) -> HostHealth:
        return self._hosts.setdefault(host, HostHealth())

    def check(self, host: str) -> None:
        reason = self.host(host).stop_reason
        if reason is not None:
            raise SourceStoppedError(host, reason)

    def stop(self, host: str, reason: str) -> None:
        health = self.host(host)
        if health.stop_reason is None:
            health.stop_reason = reason

    def observe_status(self, host: str, status_code: int) -> None:
        health = self.host(host)
        health.status_counts[str(status_code)] += 1
        if status_code in STOP_STATUS_CODES:
            self.stop(host, f"HTTP {status_code}")
            return
        if status_code >= 500:
            self._failure(host, f"HTTP {status_code}")
            return
        health.consecutive_failures = 0

    def observe_transport_error(self, host: str, error: Exception) -> None:
        self.host(host).status_counts[type(error).__name__] += 1
        self._failure(host, type(error).__name__)

    def _failure(self, host: str, reason: str) -> None:
        health = self.host(host)
        health.consecutive_failures += 1
        if health.consecutive_failures >= STOP_AFTER_CONSECUTIVE_FAILURES:
            self.stop(host, f"{health.consecutive_failures} consecutive failures, last {reason}")
        elif health.consecutive_failures >= SLOW_DOWN_AFTER_CONSECUTIVE_FAILURES:
            self._limiter.slow_down(host)


class PoliteTransport(httpx.AsyncBaseTransport):
    """Rate-limits, observes and stops; the wrapped transport does the I/O."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        limiter: DomainRateLimiter,
        health: SourceHealth,
    ) -> None:
        self._inner = inner
        self._limiter = limiter
        self._health = health

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self._health.check(host)
        await self._limiter.wait(host)
        try:
            response = await self._inner.handle_async_request(request)
        except httpx.TransportError as exc:
            self._health.observe_transport_error(host, exc)
            raise
        self._health.observe_status(host, response.status_code)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()

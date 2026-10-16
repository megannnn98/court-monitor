"""How many requests may be in flight at once, and how each request is counted.

Nothing here knows a source, a parser or a database. It gates requests (globally and
per domain), observes what was answered and how long it took, and remembers a site's
`Retry-After` so the next requests to it wait. The limits are read from the
environment so a deployment can lower them without a code change (ADR 0029).

The gates live on the transport, not on the callers: every request of a run passes
through one place, so the ceiling counts discovery, listing pages and documents alike.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Self

import httpx

__all__ = [
    "FetchLimits",
    "FetchMetrics",
    "FetchReport",
    "LimitedTransport",
    "RequestGates",
    "build_http_client",
    "parse_retry_after",
]

# robots.txt of the sources the pipeline reads, where it publishes a crawl delay.
# A domain nobody configured gets DEFAULT_PER_DOMAIN_CONCURRENCY.
DEFAULT_MAX_CONCURRENCY = 8
DEFAULT_PER_DOMAIN_CONCURRENCY = 2
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_CONNECTIONS = 20
DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 10
# Statuses that mean «come back later»: the site's own answer about how long.
COOLDOWN_STATUSES = frozenset({429, 503})


class FetchLimitsError(ValueError):
    """A configured limit that cannot mean what it says."""


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise FetchLimitsError(f"{name} must be an integer") from exc
    if value < 1:
        raise FetchLimitsError(f"{name} must be greater than 0")
    return value


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise FetchLimitsError(f"{name} must be a number") from exc
    if value <= 0:
        raise FetchLimitsError(f"{name} must be greater than 0")
    return value


def _fraction(env: Mapping[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise FetchLimitsError(f"{name} must be a number") from exc
    if not 0 <= value <= 1:
        raise FetchLimitsError(f"{name} must be between 0 and 1")
    return value


def _domain_limits(env: Mapping[str, str]) -> dict[str, int]:
    """`INGESTION_DOMAIN_CONCURRENCY="memopzk.org=1, ovd.info=3"`.

    A domain published with a crawl delay of its own (`Crawl-delay`, a request rate in
    robots.txt) is named here rather than left to the global default."""
    raw = (env.get("INGESTION_DOMAIN_CONCURRENCY") or "").strip()
    limits: dict[str, int] = {}
    if not raw:
        return limits
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        host, separator, value = item.partition("=")
        if not separator:
            raise FetchLimitsError("INGESTION_DOMAIN_CONCURRENCY entries must be `host=limit`")
        host = host.strip().lower()
        if not host:
            raise FetchLimitsError("INGESTION_DOMAIN_CONCURRENCY names no domain")
        try:
            limit = int(value.strip())
        except ValueError as exc:
            raise FetchLimitsError(
                f"INGESTION_DOMAIN_CONCURRENCY limit for {host} must be an integer"
            ) from exc
        if limit < 1:
            raise FetchLimitsError(
                f"INGESTION_DOMAIN_CONCURRENCY limit for {host} must be greater than 0"
            )
        limits[host] = limit
    return limits


@dataclass(frozen=True)
class FetchLimits:
    """The concurrency, timeouts and retry terms of one run's HTTP access."""

    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    per_domain_concurrency: int = DEFAULT_PER_DOMAIN_CONCURRENCY
    # A domain that publishes its own rate (robots.txt) or has its own backend.
    domain_concurrency: Mapping[str, int] = field(default_factory=dict)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_connections: int = DEFAULT_MAX_CONNECTIONS
    max_keepalive_connections: int = DEFAULT_MAX_KEEPALIVE_CONNECTIONS
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    # Exponential backoff is never longer than this, whatever the attempt number.
    max_delay_seconds: float = 30.0
    # The share of a backoff that is random, so retries of many documents at once do
    # not arrive together as a second burst.
    jitter_ratio: float = 0.3
    user_agent: str = "court-monitor/1.0"

    def __post_init__(self) -> None:
        for name in ("max_concurrency", "per_domain_concurrency", "max_connections"):
            if getattr(self, name) < 1:
                raise FetchLimitsError(f"{name} must be greater than 0")
        for host, limit in self.domain_concurrency.items():
            if limit < 1:
                raise FetchLimitsError(f"limit for {host} must be greater than 0")
        if self.timeout_seconds <= 0:
            raise FetchLimitsError("timeout_seconds must be greater than 0")
        if self.max_attempts < 1:
            raise FetchLimitsError("max_attempts must be greater than 0")
        if self.base_delay_seconds < 0:
            raise FetchLimitsError("base_delay_seconds must not be negative")
        if self.max_delay_seconds <= 0:
            raise FetchLimitsError("max_delay_seconds must be greater than 0")
        if not 0 <= self.jitter_ratio <= 1:
            raise FetchLimitsError("jitter_ratio must be between 0 and 1")

    def concurrency_for(self, host: str) -> int:
        """How many requests may be in flight to this domain at once."""
        return self.domain_concurrency.get(host.lower(), self.per_domain_concurrency)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> FetchLimits:
        env = os.environ if env is None else env
        return cls(
            max_concurrency=_positive_int(
                env, "INGESTION_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY
            ),
            per_domain_concurrency=_positive_int(
                env, "INGESTION_PER_DOMAIN_CONCURRENCY", DEFAULT_PER_DOMAIN_CONCURRENCY
            ),
            domain_concurrency=_domain_limits(env),
            timeout_seconds=_positive_float(
                env, "INGESTION_HTTP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
            ),
            max_connections=_positive_int(
                env, "INGESTION_MAX_CONNECTIONS", DEFAULT_MAX_CONNECTIONS
            ),
            max_keepalive_connections=_positive_int(
                env, "INGESTION_MAX_KEEPALIVE_CONNECTIONS", DEFAULT_MAX_KEEPALIVE_CONNECTIONS
            ),
            max_attempts=_positive_int(env, "INGESTION_MAX_ATTEMPTS", 3),
            base_delay_seconds=_positive_float(env, "INGESTION_RETRY_BASE_DELAY_SECONDS", 0.5),
            max_delay_seconds=_positive_float(env, "INGESTION_RETRY_MAX_DELAY_SECONDS", 30.0),
            jitter_ratio=_fraction(env, "INGESTION_RETRY_JITTER_RATIO", 0.3),
        )


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds to wait, from a `Retry-After` header: a number of seconds, or a date.

    An answer this cannot read is no answer: the caller falls back to its own backoff."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        pass
    else:
        return max(0.0, seconds)
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    reference = now if now is not None else datetime.now(tz=UTC)
    return max(0.0, (moment - reference).total_seconds())


@dataclass(frozen=True)
class FetchReport:
    """What a run's requests did. Percentiles are milliseconds."""

    requests: int
    succeeded: int
    failed: int
    retries: int
    throttled: int
    refused: int
    peak_concurrency: int
    total_seconds: float
    latency_ms: Mapping[str, float]
    status_counts: Mapping[str, int]
    error_counts: Mapping[str, int]
    per_domain: Mapping[str, DomainReport]

    @property
    def success_rate(self) -> float:
        return self.succeeded / self.requests if self.requests else 0.0


@dataclass(frozen=True)
class DomainReport:
    host: str
    requests: int
    succeeded: int
    failed: int
    retries: int
    throttled: int
    refused: int
    peak_concurrency: int
    latency_ms: Mapping[str, float]

    @property
    def success_rate(self) -> float:
        return self.succeeded / self.requests if self.requests else 0.0


def percentile(values: list[float], fraction: float) -> float:
    """The `fraction` percentile of `values` by the nearest-rank method.

    Nearest rank, not interpolation: a latency that was never observed is never
    reported as one that was."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(fraction * len(ordered) + 0.5) - 1))
    return ordered[index]


class FetchMetrics:
    """What was sent, what came back and how long it took.

    Written from the event loop of one run, but a lock guards it anyway: a test may
    share one recorder between a benchmark and a load server's threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status_counts: Counter[str] = Counter()
        self._error_counts: Counter[str] = Counter()
        self._latencies: list[float] = []
        self._domain_latencies: dict[str, list[float]] = defaultdict(list)
        self._domain_status: dict[str, Counter[str]] = defaultdict(Counter)
        self._domain_errors: dict[str, Counter[str]] = defaultdict(Counter)
        self._retries = 0
        self._domain_retries: Counter[str] = Counter()
        self._in_flight = 0
        self._peak_in_flight = 0
        self._domain_in_flight: Counter[str] = Counter()
        self._domain_peak: Counter[str] = Counter()
        self._started_at = time.monotonic()

    def started(self, host: str) -> None:
        with self._lock:
            self._in_flight += 1
            self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
            self._domain_in_flight[host] += 1
            self._domain_peak[host] = max(self._domain_peak[host], self._domain_in_flight[host])

    def finished(self, host: str, status_code: int, seconds: float) -> None:
        with self._lock:
            self._in_flight -= 1
            self._domain_in_flight[host] -= 1
            self._latencies.append(seconds)
            self._domain_latencies[host].append(seconds)
            status = str(status_code)
            self._status_counts[status] += 1
            self._domain_status[host][status] += 1

    def failed(self, host: str, error: Exception, seconds: float) -> None:
        with self._lock:
            self._in_flight -= 1
            self._domain_in_flight[host] -= 1
            self._latencies.append(seconds)
            self._domain_latencies[host].append(seconds)
            kind = type(error).__name__
            self._error_counts[kind] += 1
            self._domain_errors[host][kind] += 1

    def retried(self, host: str) -> None:
        with self._lock:
            self._retries += 1
            self._domain_retries[host] += 1

    @property
    def retries(self) -> int:
        with self._lock:
            return self._retries

    @property
    def peak_concurrency(self) -> int:
        with self._lock:
            return self._peak_in_flight

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._started_at

    def report(self) -> FetchReport:
        with self._lock:
            statuses = Counter(self._status_counts)
            errors = Counter(self._error_counts)
            latencies = list(self._latencies)
            retries = self._retries
            peak = self._peak_in_flight
            domain_retries = Counter(self._domain_retries)
            domain_peak = Counter(self._domain_peak)
            domain_status = {host: Counter(counts) for host, counts in self._domain_status.items()}
            domain_errors = {host: Counter(counts) for host, counts in self._domain_errors.items()}
            domain_latencies = {
                host: list(values) for host, values in self._domain_latencies.items()
            }
        succeeded = sum(
            count for status, count in statuses.items() if status.startswith(("2", "3"))
        )
        domains = {
            host: DomainReport(
                host=host,
                requests=len(domain_latencies[host]),
                succeeded=sum(
                    count
                    for status, count in domain_status[host].items()
                    if status.startswith(("2", "3"))
                ),
                failed=sum(
                    count
                    for status, count in domain_status[host].items()
                    if not status.startswith(("2", "3"))
                )
                + sum(domain_errors.get(host, Counter()).values()),
                retries=domain_retries[host],
                throttled=domain_status[host]["429"],
                refused=domain_status[host]["403"],
                peak_concurrency=domain_peak[host],
                latency_ms=_latency_percentiles(domain_latencies[host]),
            )
            for host in sorted(domain_latencies)
        }
        return FetchReport(
            requests=len(latencies),
            succeeded=succeeded,
            failed=len(latencies) - succeeded,
            retries=retries,
            throttled=statuses["429"],
            refused=statuses["403"],
            peak_concurrency=peak,
            total_seconds=self.elapsed_seconds(),
            latency_ms=_latency_percentiles(latencies),
            status_counts=dict(sorted(statuses.items())),
            error_counts=dict(sorted(errors.items())),
            per_domain=domains,
        )


def _latency_percentiles(values: list[float]) -> dict[str, float]:
    milliseconds = [value * 1000 for value in values]
    return {
        "p50": round(percentile(milliseconds, 0.50), 1),
        "p95": round(percentile(milliseconds, 0.95), 1),
        "p99": round(percentile(milliseconds, 0.99), 1),
        "mean": round(sum(milliseconds) / len(milliseconds), 1) if milliseconds else 0.0,
        "max": round(max(milliseconds), 1) if milliseconds else 0.0,
    }


class RequestGates:
    """A global ceiling and one per domain, plus the wait a site's own answer asked for.

    The per-domain gate is taken first: a request must never hold the global gate
    while it waits for its domain, or one slow site would spend the whole budget of
    the run on waiting."""

    def __init__(self, limits: FetchLimits) -> None:
        self._limits = limits
        self._global: asyncio.Semaphore | None = None
        self._domains: dict[str, asyncio.Semaphore] = {}
        self._cooldowns: dict[str, float] = {}

    def _global_gate(self) -> asyncio.Semaphore:
        if self._global is None:
            self._global = asyncio.Semaphore(self._limits.max_concurrency)
        return self._global

    def domain_gate(self, host: str) -> asyncio.Semaphore:
        gate = self._domains.get(host)
        if gate is None:
            gate = asyncio.Semaphore(self._limits.concurrency_for(host))
            self._domains[host] = gate
        return gate

    def cooldown_seconds(self, host: str) -> float:
        return max(0.0, self._cooldowns.get(host, 0.0) - time.monotonic())

    def note_cooldown(self, host: str, seconds: float) -> None:
        """Remember that this domain asked to be left alone for `seconds`."""
        if seconds <= 0:
            return
        until = time.monotonic() + seconds
        self._cooldowns[host] = max(self._cooldowns.get(host, 0.0), until)

    def hold(self, host: str) -> _GateHolder:
        """Wait for this domain's turn and the global budget, and return a handle to
        release both. `async with gates.hold(host)` is the whole contract."""
        return _GateHolder(self, host)


class _GateHolder:
    def __init__(self, gates: RequestGates, host: str) -> None:
        self._gates = gates
        self._host = host
        self._domain: asyncio.Semaphore | None = None
        self._global: asyncio.Semaphore | None = None

    async def __aenter__(self) -> Self:
        self._domain = self._gates.domain_gate(self._host)
        await self._domain.acquire()
        try:
            wait = self._gates.cooldown_seconds(self._host)
            if wait > 0:
                await asyncio.sleep(wait)
            self._global = self._gates._global_gate()
            await self._global.acquire()
        except BaseException:
            self._domain.release()
            raise
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        try:
            if self._global is not None:
                self._global.release()
        finally:
            if self._domain is not None:
                self._domain.release()


class LimitedTransport(httpx.AsyncBaseTransport):
    """The gate, the cooldown and the measurements of every request of a run."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        gates: RequestGates,
        *,
        metrics: FetchMetrics | None = None,
    ) -> None:
        self._inner = inner
        self._gates = gates
        self._metrics = metrics

    @property
    def gates(self) -> RequestGates:
        return self._gates

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        started = time.monotonic()
        async with self._gates.hold(host):
            if self._metrics is not None:
                self._metrics.started(host)
            try:
                response = await self._inner.handle_async_request(request)
            except httpx.TransportError as exc:
                if self._metrics is not None:
                    self._metrics.failed(host, exc, time.monotonic() - started)
                raise
            if self._metrics is not None:
                self._metrics.finished(host, response.status_code, time.monotonic() - started)
            if response.status_code in COOLDOWN_STATUSES:
                self._gates.note_cooldown(
                    host, parse_retry_after(response.headers.get("retry-after")) or 0.0
                )
            return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def build_http_client(
    limits: FetchLimits,
    *,
    metrics: FetchMetrics | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """One client for a run: pooled connections behind the gates of `limits`.

    The pool is what the previous code lacked — it built a client per request, so
    every document paid for a TCP connection and a TLS handshake."""
    inner = (
        transport
        if transport is not None
        else httpx.AsyncHTTPTransport(
            limits=httpx.Limits(
                max_connections=limits.max_connections,
                max_keepalive_connections=limits.max_keepalive_connections,
                keepalive_expiry=30.0,
            )
        )
    )
    gates = RequestGates(limits)
    return httpx.AsyncClient(
        transport=LimitedTransport(inner, gates, metrics=metrics),
        timeout=limits.timeout_seconds,
        headers={"User-Agent": limits.user_agent},
    )


def env_summary(limits: FetchLimits) -> dict[str, Any]:
    """What to print about a run's limits, secrets aside."""
    return {
        "max_concurrency": limits.max_concurrency,
        "per_domain_concurrency": limits.per_domain_concurrency,
        "domain_concurrency": dict(sorted(limits.domain_concurrency.items())),
        "timeout_seconds": limits.timeout_seconds,
        "max_connections": limits.max_connections,
        "max_attempts": limits.max_attempts,
    }

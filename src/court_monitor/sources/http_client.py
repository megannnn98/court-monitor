"""Polite HTTP client (httpx + tenacity).

* Identifiable User-Agent.
* Per-host delay + bounded retries with exponential backoff.
* Timeouts.
* Detection of soft blocks (status 403/429/captcha markers) WITHOUT bypass.

Playwright is intentionally NOT used here; it is reserved for a future
``browser`` backend for JS-rendered pages.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from court_monitor.config.settings import settings
from court_monitor.domain.models import FetchHealth

# Markers that strongly suggest a captcha / anti-bot wall. We never bypass.
_BLOCK_MARKERS = (
    "captcha",
    "проверка безопасности",
    ".areyouahuman",  # pragma: no cover - defensive literal
    "cf-challenge",  # Cloudflare interstitial
)


@dataclass
class HttpResponse:
    status: int
    text: str
    url: str
    health: FetchHealth


class HttpClient:
    """Thin wrapper around httpx.Client enforcing politeness rules."""

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        timeout: float | None = None,
        delay_seconds: float | None = None,
        max_retries: int | None = None,
        verify: str | bool | None = None,
    ) -> None:
        """``verify`` accepts a CA bundle path for hosts whose issuer is not in
        the default trust store (fedsfm.ru is signed by the Russian Ministry of
        Digital Development CA). Passing a bundle keeps verification *on* — it
        only widens which issuers count — and is scoped to the one client that
        needs it, so that CA cannot vouch for any other host we fetch.
        """
        self._user_agent = user_agent or settings.http_user_agent
        self._timeout = timeout or settings.http_timeout
        self._delay = delay_seconds if delay_seconds is not None else settings.http_delay_seconds
        self._max_retries = max_retries or settings.http_max_retries
        self._client = httpx.Client(
            headers={"User-Agent": self._user_agent},
            timeout=self._timeout,
            follow_redirects=True,
            verify=True if verify is None else verify,
        )
        self._last_request_ts: dict[str, float] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, url: str) -> HttpResponse:
        self._respect_delay(url)
        decorated = self._decorated_get()
        try:
            resp = decorated(url)
        except httpx.TimeoutException:
            return HttpResponse(0, "", url, FetchHealth.timeout)
        except httpx.TransportError:
            return HttpResponse(0, "", url, FetchHealth.http_error)
        except RetryError:  # pragma: no cover - tenacity exhausted
            return HttpResponse(0, "", url, FetchHealth.http_error)
        health = classify_response(resp.status_code, resp.text)
        return HttpResponse(resp.status_code, resp.text, str(resp.url), health)

    def _decorated_get(self):
        attempts = max(1, self._max_retries)

        @retry(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
            reraise=True,
        )
        def _get(url: str) -> httpx.Response:
            resp = self._client.get(url)
            # Retry only on 5xx; 4xx (incl. 403/429) are surfaced and NOT retried.
            if resp.status_code >= 500:
                resp.raise_for_status()
            return resp

        return _get

    def _respect_delay(self, url: str) -> None:
        host = _host_of(url)
        last = self._last_request_ts.get(host)
        if last is not None:
            elapsed = time.monotonic() - last
            wait = self._delay - elapsed
            if wait > 0:
                time.sleep(wait)
        self._last_request_ts[host] = time.monotonic()


def classify_response(status: int, body: str) -> FetchHealth:
    if status == 200:
        lowered = body.lower()
        if any(marker in lowered for marker in _BLOCK_MARKERS):
            return FetchHealth.blocked
        return FetchHealth.ok
    if status == 304:
        return FetchHealth.not_modified
    if status in (403, 429):
        return FetchHealth.blocked
    return FetchHealth.http_error


def _host_of(url: str) -> str:
    try:
        return httpx.URL(url).host
    except Exception:  # pragma: no cover - defensive
        return url

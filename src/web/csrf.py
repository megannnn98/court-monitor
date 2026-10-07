"""Cross-site request forgery: an unsafe request is served only from the console's own
origin (ADR 0022, option A).

The console has no login: whoever reaches the port is trusted. What this stops is a
page in the operator's browser submitting a form to `127.0.0.1:8001` — a cross-site
form POST needs no preflight, so the missing CORS does not stop it.

A browser says where a request comes from by itself: `Sec-Fetch-Site`, `Origin`, else
`Referer`. A request with none of them is not a browser's (curl, scripts) and needs no
such check: forgery needs a victim's browser. This is not authentication.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request, Response

from web.errors import error_response

logger = logging.getLogger("api")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# The local console and the Vite dev server; `ALLOWED_ORIGINS` replaces the list.
DEFAULT_ALLOWED_ORIGINS = (
    "http://127.0.0.1:8001",
    "http://localhost:8001",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)

__all__ = ["DEFAULT_ALLOWED_ORIGINS", "allowed_origins", "refused_origin", "same_origin_only"]


def allowed_origins() -> frozenset[str]:
    """Origins allowed besides the request's own host, from `ALLOWED_ORIGINS`
    (comma-separated); the local ones by default."""
    raw = os.environ.get("ALLOWED_ORIGINS")
    if raw is None:
        return frozenset(DEFAULT_ALLOWED_ORIGINS)
    return frozenset(item.strip().rstrip("/") for item in raw.split(",") if item.strip())


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def refused_origin(
    method: str, host: str | None, headers: dict[str, str], allowed: frozenset[str]
) -> str | None:
    """The origin an unsafe request came from when it is not the console's; None when
    the request may be served. `headers` are lower-cased names."""
    if method.upper() in SAFE_METHODS:
        return None
    if headers.get("sec-fetch-site") == "same-origin":
        return None
    origin = headers.get("origin")
    if origin is None:
        referer = headers.get("referer")
        if referer is None:
            # Not a browser: nothing to forge with.
            return None
        origin = _origin_of(referer) or referer
    if origin == "null":
        return origin
    parts = urlsplit(origin)
    if host and parts.netloc == host:
        return None
    if origin.rstrip("/") in allowed:
        return None
    return origin


async def same_origin_only(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """403 for an unsafe request from another origin; everything else passes."""
    headers = {
        name: request.headers[name]
        for name in ("sec-fetch-site", "origin", "referer")
        if name in request.headers
    }
    refused = refused_origin(
        request.method, request.headers.get("host"), headers, allowed_origins()
    )
    if refused is not None:
        logger.warning(
            "event=csrf_refused method=%s path=%s origin=%s sec_fetch_site=%s",
            request.method,
            request.url.path,
            refused,
            headers.get("sec-fetch-site", "-"),
        )
        return error_response(403, "csrf_refused", f"cross-origin request refused: {refused}")
    return await call_next(request)

"""A demo stand: reads alone, and starting an investigation (ADR 0029).

`DEMO_READ_ONLY=1` turns it on. The console has no login, and a demo is open to anyone
with its link: whatever would change the data — a pipeline stage, a review, a purge, a
sync — is refused before its route runs. An investigation is let through: on a demo it
runs on rules alone and writes only its own run.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from web.csrf import SAFE_METHODS
from web.errors import error_response

logger = logging.getLogger("api")

# (method, path) a demo serves although they are not reads.
EXEMPT = frozenset({("POST", "/api/v1/investigation")})

__all__ = ["EXEMPT", "read_only", "read_only_demo", "refused_by_demo"]


def read_only() -> bool:
    return os.environ.get("DEMO_READ_ONLY", "").strip() == "1"


def refused_by_demo(method: str, path: str) -> bool:
    method = method.upper()
    return method not in SAFE_METHODS and (method, path) not in EXEMPT


async def read_only_demo(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """403 for a change on a demo stand; everything passes elsewhere."""
    if read_only() and refused_by_demo(request.method, request.url.path):
        logger.info("event=demo_refused method=%s path=%s", request.method, request.url.path)
        return error_response(403, "demo_read_only", "демо-стенд только для просмотра")
    return await call_next(request)

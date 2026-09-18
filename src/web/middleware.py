"""Request context: a request id for logs and responses; unhandled errors never leak a traceback."""

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from observability import REQUEST_ID_HEADER, normalize_request_id, request_id_var
from web.errors import error_response

logger = logging.getLogger("api")


async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Request id for logs and responses; unhandled errors never leak a traceback."""
    request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
    token = request_id_var.set(request_id)
    started = time.monotonic()
    try:
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception(
                "event=http_unhandled_error method=%s path=%s error_kind=%s",
                request.method,
                request.url.path,
                type(exc).__name__,
            )
            response = error_response(500, "internal_error", "Internal server error")
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "event=http_request method=%s path=%s status=%s duration_ms=%d",
            request.method,
            request.url.path,
            response.status_code,
            (time.monotonic() - started) * 1000,
        )
        return response
    finally:
        request_id_var.reset(token)

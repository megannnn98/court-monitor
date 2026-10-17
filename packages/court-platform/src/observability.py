"""Request correlation and consistent log fields for the application adapters.

Log messages use `event=<name> key=value ...`; every record also carries the
current `request_id` (or `-` outside a request), so API logs of one request can
be grepped together. No third-party logging framework.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-ID"
# Caller-supplied ids are kept only if short and plain; anything else is replaced.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def normalize_request_id(value: str | None) -> str:
    if value is not None and _VALID_REQUEST_ID.fullmatch(value):
        return value
    return uuid.uuid4().hex


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent: adds the request-id field to the root handlers."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format=LOG_FORMAT)
    for handler in root.handlers:
        if not any(isinstance(existing, RequestIdFilter) for existing in handler.filters):
            handler.addFilter(RequestIdFilter())
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.setLevel(min(root.level or level, level))

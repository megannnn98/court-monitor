"""Shared web-layer concerns: operator identity, CSRF, session handling.

Every mutating route goes through :func:`require_operator`. Today it resolves a
name from configuration, because the tool is expected to run bound to
localhost. When real authentication arrives (D-007) only this function changes
— routes already depend on it and already record whoever it returns in the
audit log, so no endpoint has to be rewritten to become authenticated.
"""

from __future__ import annotations

import getpass
import hmac
import secrets
from collections.abc import Iterator

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from court_monitor.config.settings import settings
from court_monitor.storage.db import make_engine, make_session_factory

_engine = make_engine()
_session_factory = make_session_factory(_engine)

# One token per process. Enough to stop a third-party page from POSTing to a
# localhost-bound tool; it is not a substitute for the authentication that
# D-007 tracks, and deliberately does not pretend to be.
_CSRF_TOKEN = secrets.token_urlsafe(32)
CSRF_FIELD = "csrf_token"


def get_session() -> Iterator[Session]:
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


def require_operator() -> str:
    """Identity recorded against every decision in the audit log."""
    configured = (settings.web_operator or "").strip()
    if configured:
        return configured
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - no login name available
        return "unknown"


def csrf_token() -> str:
    return _CSRF_TOKEN


async def verify_csrf(request: Request) -> None:
    """Reject a mutating request that did not carry this process's token."""
    form = await request.form()
    submitted = str(form.get(CSRF_FIELD, ""))
    if not hmac.compare_digest(submitted, _CSRF_TOKEN):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")

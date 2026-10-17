"""The entity model the console shows: the stored choice, read at most every few seconds.

The warnings before a step are drawn by many pages from many places; each asks here
rather than carrying a session. A start reads the choice from the database itself
(`orchestrator.operations.OperationRegistry.start`), so what runs never depends on this
cache. Without a database (a test, a broken pool) the page names the environment's model
and logs why.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from sqlalchemy.exc import SQLAlchemyError

from llm.chat import Purpose
from llm.model_choice import environment_model, stored_model

logger = logging.getLogger("operator_console")

CACHE_SECONDS = 5.0

_lock = threading.Lock()
_cache: dict[Purpose, tuple[float, str]] = {}


def _read_stored(kind: Purpose = "extraction") -> str | None:
    from web.dependencies import _get_session_factory

    with _get_session_factory()() as session:
        return stored_model(session, kind)


# Replaced in tests: no test reads a real database through a page's warning.
read_stored: Callable[[Purpose], str | None] = _read_stored


def current_entity_model(
    kind: Purpose = "extraction", now: Callable[[], float] = time.monotonic
) -> str:
    with _lock:
        cached = _cache.get(kind)
        if cached is not None and now() - cached[0] < CACHE_SECONDS:
            return cached[1]
    try:
        model = read_stored(kind) or environment_model(kind=kind)
    except (RuntimeError, SQLAlchemyError) as exc:
        logger.warning(
            "event=entity_model_choice_unreadable kind=%s error=%s", kind, type(exc).__name__
        )
        model = environment_model(kind=kind)
    with _lock:
        _cache[kind] = (now(), model)
    return model


def forget() -> None:
    """After a save: the next page reads the new choice."""
    with _lock:
        _cache.clear()

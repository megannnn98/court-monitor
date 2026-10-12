"""What an investigation tells the log and its counters.

One line per event, `event=investigation_<name>` with key=value pairs, as the rest of
the project logs; and a process counter per event with the summed durations, read by
`snapshot()`. A query is logged by a hash and its length, never its words; no text of a
publication, prompt or credential is ever logged.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import Counter
from collections.abc import Mapping

logger = logging.getLogger("investigation")

EVENTS = (
    "started",
    "plan_built",
    "search_started",
    "search_completed",
    "search_failed",
    "coverage_assessed",
    "followup_started",
    "completed",
    "failed",
)

_lock = threading.Lock()
_counts: Counter[str] = Counter()
_durations_ms: Counter[str] = Counter()


def query_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()[:12]


def emit(
    event: str,
    *,
    duration_ms: float | None = None,
    context: Mapping[str, object] | None = None,
    **fields: object,
) -> None:
    if event not in EVENTS:
        raise ValueError(f"unknown investigation event: {event}")
    with _lock:
        _counts[event] += 1
        if duration_ms is not None:
            _durations_ms[event] += int(duration_ms)
    parts = [f"event=investigation_{event}"]
    if duration_ms is not None:
        parts.append(f"duration_ms={int(duration_ms)}")
    parts.extend(f"{key}={value}" for key, value in {**(context or {}), **fields}.items())
    level = logging.WARNING if event in ("search_failed", "failed") else logging.INFO
    logger.log(level, " ".join(parts))


def snapshot() -> dict[str, dict[str, int]]:
    with _lock:
        return {
            event: {"count": _counts[event], "duration_ms": _durations_ms[event]}
            for event in EVENTS
        }

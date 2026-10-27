"""What a running step is doing, for any page that shows one: its last three log lines and
whether the process still answers.

A step that loads articles or waits for a model can be silent on the page for minutes, and
a page that says «выполняется» and nothing else cannot be told from one that has hung. So
every page that shows a live run shows the same two things under it: the last three things
the run said, and when its process last gave a sign of life (the heartbeat the runner keeps).
A run that ended badly keeps its last lines, for the reason; one that ended well needs none.
"""

from __future__ import annotations

import re

from orchestrator.operations import OperationRunStatus

# The lines of a log that say nothing about the work: one request after another to a site.
_NOISE = ("HTTP Request:", " httpx ")

_LOG_LINE = re.compile(
    r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ \w+ \S+ (?:request_id=\S+ )?(?P<rest>.*)$"
)

LOG_TAIL_LINES = 3

LOG_TAIL_WIDTH = 160

_LIVE = (OperationRunStatus.PENDING, OperationRunStatus.RUNNING)

_ENDED_BADLY = (OperationRunStatus.FAILED, OperationRunStatus.INTERRUPTED)


def log_tail(stderr: str, count: int = LOG_TAIL_LINES) -> list[str]:
    """The last few things a run said: the latest line of each of the last `count` events.

    A step that asks a model logs one line per batch, and three of those say one thing; so a
    line counts once per event, and what is shown is where the run is now and what it did
    just before. Date, level and request id are cut off: the card says when it started."""
    found: list[str] = []
    events: set[str] = set()
    for raw in reversed(stderr.splitlines()):
        if not raw.strip() or any(noise in raw for noise in _NOISE):
            continue
        match = _LOG_LINE.match(raw)
        line = match.group("rest") if match else raw.strip()
        event = next((word for word in line.split() if word.startswith("event=")), line[:40])
        if event in events:
            continue
        events.add(event)
        found.append(line[:LOG_TAIL_WIDTH])
        if len(found) == count:
            break
    return list(reversed(found))


def _ago(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    return f"{seconds} с" if seconds < 60 else f"{seconds // 60} мин"

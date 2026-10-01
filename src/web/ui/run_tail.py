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
from datetime import UTC, datetime
from html import escape

from operator_console import STALE_AFTER, OperationRun, OperationRunStatus

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


def pulse(run: OperationRun, now: datetime | None = None) -> str:
    """Whether the process of a live run still answers, in words; the class of the line."""
    now = now or datetime.now(UTC)
    if run.status is OperationRunStatus.PENDING or run.started_at is None:
        return '<p class="muted log-pulse">Запуск готовится…</p>'
    signal = run.heartbeat_at or run.started_at
    if signal.tzinfo is None:
        signal = signal.replace(tzinfo=UTC)
    age = (now - signal).total_seconds()
    if age >= STALE_AFTER.total_seconds():
        return (
            f'<p class="warning log-pulse">Сигнала от процесса нет уже {_ago(age)}: он, '
            "возможно, потерян. Запуск сам отметится остановленным.</p>"
        )
    return (
        f'<p class="muted log-pulse">Процесс отвечает: последний сигнал {_ago(age)} назад. '
        "Страница обновляется сама.</p>"
    )


def tail_html(run: OperationRun, now: datetime | None = None) -> str:
    """The block under a run: while it runs, whether it is alive and its last three lines
    (or a word that it has said nothing yet); after a bad end, the lines; after a good one,
    nothing."""
    lines = log_tail(run.stderr)
    if run.status in _LIVE:
        shown = (
            '<pre class="log-tail" aria-label="Последние строки журнала">'
            + escape("\n".join(lines))
            + "</pre>"
            if lines
            else '<p class="muted">Процесс пока ничего не написал в журнал.</p>'
        )
        return f"{pulse(run, now)}{shown}"
    if run.status in _ENDED_BADLY and lines:
        return (
            '<pre class="log-tail" aria-label="Последние строки журнала">'
            + escape("\n".join(lines))
            + "</pre>"
        )
    return ""

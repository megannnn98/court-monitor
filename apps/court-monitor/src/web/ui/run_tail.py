"""What a running step is doing, for any page that shows one: its last three log lines and
whether the process still answers.

A step that loads articles or waits for a model can be silent on the page for minutes, and
a page that says «выполняется» and nothing else cannot be told from one that has hung. So
every page that shows a live run shows the same two things under it: the last three things
the run said, and when its process last gave a sign of life (the heartbeat the runner keeps).
A run that ended badly keeps its last lines, for the reason; one that ended well needs none.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from orchestrator.operations import STALE_AFTER, OperationRun, OperationRunStatus
from web.views.run_tail import _ENDED_BADLY, _LIVE, _ago, log_tail


def pulse(run: OperationRun, now: datetime | None = None) -> str:
    """Whether the process of a live run still answers, in words."""
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
    shown = (
        f'<pre class="log-tail" aria-label="Последние строки журнала">{escape("\n".join(lines))}'
        "</pre>"
        if lines
        else ""
    )
    if run.status in _LIVE:
        silent = '<p class="muted">Процесс пока ничего не написал в журнал.</p>'
        return f"{pulse(run, now)}{shown or silent}"
    return shown if run.status in _ENDED_BADLY else ""

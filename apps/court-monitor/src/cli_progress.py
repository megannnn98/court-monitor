"""A one-line progress bar for the long CLI stages.

Silent unless the stream is a terminal, so redirected output and CI logs keep the
plain result lines the commands already print and never collect carriage returns.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from types import TracebackType
from typing import IO, Self

BAR_WIDTH = 30
# Redraws are throttled: a stage doing thousands of steps must not spend its time writing.
MIN_REDRAW_SECONDS = 0.2


def _format_eta(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes >= 60:
        return f"{minutes // 60}h{minutes % 60:02d}m"
    if minutes >= 1:
        return f"{minutes}m"
    return f"{int(seconds)}s"


class ProgressBar:
    """Progress of one stage, redrawn in place on `stream`.

    Every method is a no-op when the stream is not a terminal, so callers can use it
    unconditionally.
    """

    def __init__(
        self,
        label: str,
        total: int,
        *,
        stream: IO[str] | None = None,
        now: Callable[[], float] = time.monotonic,
        width: int = BAR_WIDTH,
        min_redraw_seconds: float = MIN_REDRAW_SECONDS,
    ) -> None:
        self._label = label
        self._total = max(total, 0)
        self._stream = stream if stream is not None else sys.stderr
        self._now = now
        self._width = width
        self._min_redraw_seconds = min_redraw_seconds
        self._done = 0
        self._started_at = now()
        self._last_redraw_at: float | None = None
        self._enabled = self._total > 0 and bool(getattr(self._stream, "isatty", bool)())

    def advance(self, step: int = 1) -> None:
        if not self._enabled:
            return
        self._done = min(self._done + step, self._total)
        moment = self._now()
        if (
            self._last_redraw_at is not None
            and self._done < self._total
            and moment - self._last_redraw_at < self._min_redraw_seconds
        ):
            return
        self._last_redraw_at = moment
        self._stream.write(f"\r{self._render(moment)}\033[K")
        self._stream.flush()

    def close(self) -> None:
        """Leave the finished bar on its own line so the result lines follow it."""
        if not self._enabled or self._last_redraw_at is None:
            return
        self._stream.write("\n")
        self._stream.flush()
        self._enabled = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _render(self, moment: float) -> str:
        filled = self._done * self._width // self._total
        bar = "#" * filled + "." * (self._width - filled)
        percent = self._done * 100 // self._total
        line = f"{self._label} [{bar}] {percent:3d}%  {self._done}/{self._total}"

        elapsed = moment - self._started_at
        if self._done == 0 or elapsed <= 0:
            return line

        per_minute = self._done / elapsed * 60
        remaining = (self._total - self._done) / (self._done / elapsed)
        return f"{line}  {per_minute:.0f}/min, ~{_format_eta(remaining)} left"

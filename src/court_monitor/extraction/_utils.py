"""Shared utilities for extraction modules."""

from __future__ import annotations

import re


def quote_around(text: str, start: int, end: int, *, window: int = 40) -> str:
    """Extract a context snippet around a match position."""
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    return re.sub(r"\s+", " ", text[lo:hi]).strip()


def overlaps(spans: set[tuple[int, int]], start: int, end: int) -> bool:
    """True when ``[start, end)`` touches a span already claimed.

    Extractors sweep the same text with several patterns, most specific first,
    and each sweep skips what an earlier one matched — so a single reference is
    recorded once at its highest confidence instead of several times at
    descending ones.
    """
    return any(start < e and end > s for s, e in spans)

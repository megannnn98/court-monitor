"""Shared utilities for extraction modules."""

from __future__ import annotations

import re


def quote_around(text: str, start: int, end: int, *, window: int = 40) -> str:
    """Extract a context snippet around a match position."""
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    return re.sub(r"\s+", " ", text[lo:hi]).strip()

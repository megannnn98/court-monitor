"""One conservative spelling of Criminal Code charges for leads and verification."""

from __future__ import annotations

import re

CHARGE = re.compile(
    r"(?<![а-яёa-z])"
    r"(?:ч(?:асть|асти|\.)\s*(?P<part>\d+)(?:\s*(?:и|,)\s*ч(?:асть|асти|\.)\s*\d+)?\s*)?"
    r"ст(?:атья|атьи|атье|атью|атьей|атьёй|атьям|атьями|\.)\s*(?P<article>\d+(?:\.\d+)*)\s*"
    r"(?:ук(?:\s*рф)?|уголовного\s+кодекса(?:\s+(?:рф|российской\s+федерации))?)",
    re.IGNORECASE,
)


def normalised_charges(text: str) -> set[str]:
    """Every Criminal Code charge written in text; other codes never become УК."""
    return {
        f"{'ч. ' + match.group('part') + ' ' if match.group('part') else ''}"
        f"ст. {match.group('article')} УК РФ"
        for match in CHARGE.finditer(text)
    }

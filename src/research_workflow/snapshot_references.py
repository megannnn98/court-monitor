"""Deterministic detection of snapshot ids the user explicitly referenced.

A bare number in the query ("3 человека", "2024 год") is never a snapshot
reference; only a number directly attached to the word snapshot/снапшот is.
"""

from __future__ import annotations

import re

_SNAPSHOT_REFERENCE = re.compile(
    r"(?<![\w])(?:snapshot(?:_id)?|сн[аэ]пшот[а-яё]*)"
    r"\s*(?:#|№|=|:)?\s*(?:номер\s*)?(\d+)(?!\d)",
    re.IGNORECASE,
)


def extract_explicit_snapshot_ids(query: str) -> list[int]:
    """Distinct explicitly referenced snapshot ids, in order of appearance."""
    ids: list[int] = []
    for match in _SNAPSHOT_REFERENCE.finditer(query):
        snapshot_id = int(match.group(1))
        if snapshot_id not in ids:
            ids.append(snapshot_id)
    return ids

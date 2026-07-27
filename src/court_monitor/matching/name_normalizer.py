"""Name normalization for person matching.

Produces a comparable representation of a Russian name:
- lowercase, ё→е, punctuation removal
- surname / name / patronymic extraction
- initial extraction
- confidence based on token count

Never modifies the original ExtractedFact value.
"""

from __future__ import annotations

from dataclasses import dataclass

from court_monitor.normalization import normalize_fio


@dataclass(frozen=True)
class NormalizedName:
    """Comparable name representation."""

    raw: str
    tokens: list[str]
    surname: str
    name: str
    patronymic: str
    initials: str
    method: str
    confidence: float


# Common patronymic suffixes (masculine + feminine)
_PATRONYMIC_SUFFIXES = ("ович", "евич", "ич", "овна", "евна", "ична", "инична")


def normalize_name(raw: str) -> NormalizedName:
    """Normalize a Russian name into structured components.

    Handles:
    - Иванов Иван Иванович (full, nominative)
    - Иванова Ивана Ивановича (oblique cases)
    - Иванов И. И. (initials)
    - Иван-Петров Иван Иванович (hyphenated surnames)
    """
    if not raw:
        return NormalizedName(
            raw=raw, tokens=[], surname="", name="", patronymic="",
            initials="", method="empty", confidence=0.0,
        )

    normalized = normalize_fio(raw)
    tokens = normalized.split()

    surname = ""
    name = ""
    patronymic = ""
    confidence = 0.0
    method = "rules_v1"

    if len(tokens) >= 3:
        surname = tokens[0]
        name = tokens[1]
        patronymic = tokens[2]
        confidence = 0.85
    elif len(tokens) == 2:
        surname = tokens[0]
        name = tokens[1]
        confidence = 0.60
    elif len(tokens) == 1:
        surname = tokens[0]
        confidence = 0.30

    # Build initials string
    initials_parts = []
    if surname:
        initials_parts.append(surname[0])
    if name:
        initials_parts.append(name[0])
    if patronymic:
        initials_parts.append(patronymic[0])
    initials = "".join(initials_parts)

    # Trim patronymic suffix for oblique case matching
    # e.g. "ивановича" → "иванович", "петровну" → "петровн"
    # This is a heuristic — not morphological analysis
    if patronymic:
        for suffix in ("а", "у", "ы", "е", "ой", "ом"):
            if patronymic.endswith(suffix) and len(patronymic) > len(suffix) + 2:
                trimmed = patronymic[: -len(suffix)]
                # Check if trimmed form looks like a patronymic
                if any(trimmed.endswith(s) for s in _PATRONYMIC_SUFFIXES):
                    patronymic = trimmed
                    break

    return NormalizedName(
        raw=raw,
        tokens=tokens,
        surname=surname,
        name=name,
        patronymic=patronymic,
        initials=initials,
        method=method,
        confidence=confidence,
    )

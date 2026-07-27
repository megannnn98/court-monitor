"""Name normalization for person matching — ru-name-v2.

Deterministic morphological rules for Russian name case normalization.
Converts oblique cases to nominative for comparison:

  Иванова Ивана Ивановича → Иванов Иван Иванович
  Петрову Марию Сергеевну → Петрова Мария Сергеевна

Rules are transparent and auditable. Not a full morphological analyzer —
handles common patterns only. Ambiguous transformations are flagged.

The raw value is always preserved; normalization is a separate field.
"""

from __future__ import annotations

from dataclasses import dataclass

from court_monitor.normalization import normalize_fio


@dataclass(frozen=True)
class NormalizedName:
    """Comparable name representation."""

    raw: str
    nominative: str  # morphological nominative form
    tokens: list[str]
    surname: str
    name: str
    patronymic: str
    initials: str
    method: str
    confidence: float


# ---------------------------------------------------------------------------
# Surnames: masculine → feminine and oblique cases
# ---------------------------------------------------------------------------

# Masculine → feminine: -ов → -ова, -ев → -ева, -ин → -ина
SurnameRule = tuple[str, str]  # (pattern_suffix, replacement)

_MASC_TO_FEM: list[SurnameRule] = [
    ("ов", "ова"),
    ("ев", "ева"),
    ("ин", "ина"),
    ("цкий", "цкая"),
    ("ский", "ская"),
    ("ной", "ная"),
    ("ой", "ая"),
]

# Masculine oblique: -а, -у, -ом, -ым, -е
SurnameObliqueMasculine = [
    ("у", "", "m"),    # Иванову → Иванов
    ("ом", "", "m"),   # Ивановым → Иванов
    ("ым", "", "m"),   # Ивановым → Иванов (instrumental)
    ("е", "", "m"),    # Иванове → Иванов (locative)
    ("а", "", "m"),    # Иванова → Иванов (genitive/accusative)
]

# Feminine oblique: -у, -овой, -евной
SurnameObliqueFeminine = [
    ("у", "а", "f"),       # Петрову → Петрова (accusative)
    ("овой", "ова", "f"),  # Петровой → Петрова (genitive/dative)
    ("евной", "евна", "f"),  # Ивановной → Ивановна
    ("овной", "овна", "f"),  # Ивановной → Ивановна
]

# Patronymic oblique suffixes
_PATRONYMIC_OBLIQUE = [
    # Masculine
    ("овичу", "ович"),
    ("евичу", "евич"),
    ("ичу", "ич"),
    ("овичем", "ович"),
    ("евичем", "евич"),
    ("ичем", "ич"),
    ("овиче", "ович"),
    ("евиче", "евич"),
    ("иче", "ич"),
    ("овича", "ович"),
    ("евича", "евич"),
    ("ича", "ич"),
    # Feminine
    ("овне", "овна"),
    ("евне", "евна"),
    ("ичне", "ична"),
    ("овну", "овна"),
    ("евну", "евна"),
    ("ичну", "ична"),
    ("овной", "овна"),
    ("евной", "евна"),
    ("ичной", "ична"),
    ("овны", "овна"),  # genitive: Сергеевны → Сергеевна
    ("евны", "евна"),  # genitive: Алексеевны → Алексеевна
    ("ичны", "ична"),  # genitive: Иваничны → Иванична
]

# Masculine name oblique
_NAME_OBLIQUE_M = [
    ("а", ""),     # Ивана → Иван
    ("у", ""),     # Ивану → Иван
    ("ом", ""),    # Иваном → Иван
    ("е", ""),     # Иване → Иван
    ("я", ""),     # Никитя → Никита (rare)
]

# Feminine name oblique
_NAME_OBLIQUE_F = [
    ("ию", "ия"),  # Марию → Мария (accusative for -ия names)
    ("ю", "я"),    # Наталью → Наталья (accusative)
    ("у", "а"),    # Марину → Марина
    ("ии", "ия"),  # Марии → Мария (genitive/dative for -ия names)
    ("ой", "а"),   # Марией → Марина (actually instrumental)
    ("ей", "а"),   # Мариею → Марина
    ("ею", "а"),   # Мариею → Марина
    ("е", "а"),    # Марии → Марина (dative/prepositional)
]

# Feminine names ending in -а/-я → oblique patterns
_FEMININE_NAME_SUFFIXES = ("а", "я", "ья")


def normalize_name_morph(raw: str) -> NormalizedName:
    """Normalize a Russian name to nominative using morphological rules.

    Handles:
    - Иванова Ивана Ивановича → Иванов Иван Иванович
    - Петрову Марию Сергеевну → Петрова Мария Сергеевна
    - Иванов И. И. (initials — no morph change)
    """
    if not raw:
        return NormalizedName(
            raw=raw, nominative="", tokens=[], surname="", name="",
            patronymic="", initials="", method="empty", confidence=0.0,
        )

    normalized = normalize_fio(raw)
    tokens = normalized.split()

    if len(tokens) < 3:
        # Can't do morphological normalization without 3 tokens
        return _simple_parse(raw, normalized, tokens)

    surname_raw = tokens[0]
    name_raw = tokens[1]
    patronymic_raw = tokens[2]

    # Detect gender from patronymic
    gender = _detect_gender(patronymic_raw)

    # Normalize surname to nominative
    surname_nom = _normalize_surname(surname_raw, gender)

    # Normalize name to nominative
    name_nom = _normalize_name(name_raw, gender)

    # Normalize patronymic to nominative
    patronymic_nom = _normalize_patronymic(patronymic_raw)

    # Build nominative form
    nominative = f"{surname_nom} {name_nom} {patronymic_nom}"

    # Build initials
    initials_parts = []
    if surname_nom:
        initials_parts.append(surname_nom[0])
    if name_nom:
        initials_parts.append(name_nom[0])
    if patronymic_nom:
        initials_parts.append(patronymic_nom[0])
    initials = "".join(initials_parts)

    # Check if transformation was confident
    confidence = 0.85
    if surname_nom != surname_raw or name_nom != name_raw:
        confidence = 0.75  # morphological transformation — slightly less confident

    return NormalizedName(
        raw=raw,
        nominative=nominative,
        tokens=[surname_nom, name_nom, patronymic_nom],
        surname=surname_nom,
        name=name_nom,
        patronymic=patronymic_nom,
        initials=initials,
        method="ru-name-v2",
        confidence=confidence,
    )


def _simple_parse(raw: str, normalized: str, tokens: list[str]) -> NormalizedName:
    """Parse a name with fewer than 3 tokens (no morphological normalization)."""
    surname = tokens[0] if len(tokens) >= 1 else ""
    name = tokens[1] if len(tokens) >= 2 else ""
    patronymic = tokens[2] if len(tokens) >= 3 else ""

    initials_parts = []
    if surname:
        initials_parts.append(surname[0])
    if name:
        initials_parts.append(name[0])
    if patronymic:
        initials_parts.append(patronymic[0])
    initials = "".join(initials_parts)

    confidence = 0.60 if len(tokens) == 2 else 0.30

    return NormalizedName(
        raw=raw,
        nominative=normalized,
        tokens=tokens,
        surname=surname,
        name=name,
        patronymic=patronymic,
        initials=initials,
        method="ru-name-v2-simple",
        confidence=confidence,
    )


def _detect_gender(patronymic: str) -> str:
    """Detect gender from patronymic ending."""
    if not patronymic:
        return "unknown"
    p = patronymic.lower()
    if p.endswith(("вич", "вича", "вичу", "вичем", "виче")):
        return "m"
    if p.endswith(("вна", "вны", "вну", "вной", "вне")):
        return "f"
    if p.endswith(("ич", "ича", "ичу", "ичем", "иче")):
        return "m"
    if p.endswith(("ична", "ичны", "ичну", "ичной", "ичне")):
        return "f"
    return "unknown"


def _normalize_surname(surname: str, gender: str) -> str:
    """Normalize surname to nominative."""
    s = surname.lower()

    # Try feminine oblique first (more specific)
    if gender == "f":
        for oblique, nominative, _ in SurnameObliqueFeminine:
            if s.endswith(oblique):
                base = s[: -len(oblique)]
                candidate = base + nominative if nominative else base
                # Verify it looks like a valid surname
                if len(candidate) >= 3:
                    return candidate

    # Try masculine oblique
    if gender == "m":
        for oblique, nominative, _ in SurnameObliqueMasculine:
            if s.endswith(oblique):
                base = s[: -len(oblique)]
                candidate = base + nominative if nominative else base
                if len(candidate) >= 3:
                    return candidate

    # If gender unknown, try both
    if gender == "unknown":
        for oblique, nominative, _g in SurnameObliqueMasculine + SurnameObliqueFeminine:
            if s.endswith(oblique):
                base = s[: -len(oblique)]
                candidate = base + nominative if nominative else base
                if len(candidate) >= 3:
                    return candidate

    return s


def _normalize_name(name: str, gender: str) -> str:
    """Normalize first name to nominative."""
    n = name.lower()

    if gender == "f":
        for oblique, nominative in _NAME_OBLIQUE_F:
            if n.endswith(oblique):
                base = n[: -len(oblique)]
                candidate = base + nominative if nominative else base
                if len(candidate) >= 2:
                    return candidate

    if gender == "m":
        for oblique, nominative in _NAME_OBLIQUE_M:
            if n.endswith(oblique):
                base = n[: -len(oblique)]
                candidate = base + nominative if nominative else base
                if len(candidate) >= 2:
                    return candidate

    return n


def _normalize_patronymic(patronymic: str) -> str:
    """Normalize patronymic to nominative."""
    p = patronymic.lower()

    for oblique, nominative in _PATRONYMIC_OBLIQUE:
        if p.endswith(oblique):
            base = p[: -len(oblique)]
            return base + nominative

    return p

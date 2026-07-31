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

# Masculine oblique: order matters — longer suffixes first
_SURNAME_OBLIQUE_M = [
    ("ом", ""),  # Ивановым → Иванов
    ("ым", ""),  # Ивановым → Иванов (instrumental)
    ("у", ""),  # Иванову → Иванов
    ("е", ""),  # Иванове → Иванов (locative)
    ("а", ""),  # Иванова → Иванов (genitive/accusative)
]

# Feminine oblique: order matters — longer suffixes first
_SURNAME_OBLIQUE_F = [
    ("евой", "ева"),  # Марьевой → Марьева
    ("овой", "ова"),  # Петровой → Петрова (genitive/dative)
    ("ью", "ья"),  # Наталью → Наталья (accusative for -ья names)
    ("ую", "ая"),  # Козловую → Козлова
    ("у", "а"),  # Петрову → Петрова (accusative)
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
    ("а", ""),  # Ивана → Иван
    ("у", ""),  # Ивану → Иван
    ("ом", ""),  # Иваном → Иван
    ("е", ""),  # Иване → Иван
    ("я", ""),  # Никитя → Никита (rare)
]

# Feminine name oblique: order matters — longer suffixes first
_NAME_OBLIQUE_F = [
    ("ию", "ия"),  # Марию → Мария (accusative for -ия names)
    ("ью", "ья"),  # Софью → Софья (accusative for -ья names)
    ("ю", "я"),  # Наталью → Наталья (accusative for -я names)
    ("у", "а"),  # Марину → Марина
    ("ии", "ия"),  # Марии → Мария (genitive/dative for -ия names)
    ("ой", "а"),  # Марией → Марина (actually instrumental)
    ("ей", "а"),  # Мариею → Марина
    ("ею", "а"),  # Мариею → Марина
    ("е", "а"),  # Марии → Марина (dative/prepositional)
]


def _initials(*parts: str) -> str:
    """First letter of each part that has one."""
    return "".join(part[0] for part in parts if part)


def normalize_name_morph(raw: str) -> NormalizedName:
    """Normalize a Russian name to nominative using morphological rules.

    Handles:
    - Иванова Ивана Ивановича → Иванов Иван Иванович
    - Петрову Марию Сергеевну → Петрова Мария Сергеевна
    - Иванов И. И. (initials — no morph change)
    """
    if not raw:
        return NormalizedName(
            raw=raw,
            nominative="",
            tokens=[],
            surname="",
            name="",
            patronymic="",
            initials="",
            method="empty",
            confidence=0.0,
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
    initials = _initials(surname_nom, name_nom, patronymic_nom)

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


# ---------------------------------------------------------------------------
# Two-token order detection ("Имя Фамилия" vs "Фамилия Имя")
# ---------------------------------------------------------------------------
#
# A three-token name is anchored by its patronymic, so the surname is known to
# come first. A two-token name has no such anchor, and the two sides of the
# system disagree on order: news text writes "Владимир Романов" (given name
# first) while the RFM registry writes "Романов Владимир Петрович". Taking
# tokens[0] unconditionally therefore files an article's *given* name in the
# surname field, and the surname index can never match it against a record.

# Endings that mark a Russian/post-Soviet surname. Feminine forms are listed
# explicitly because "-ова" does not end in "-ов".
_SURNAME_SUFFIXES = (
    "ов",
    "ев",
    "ин",
    "ын",  # Иванов, Лебедев, Пушкин, Птицын
    "ова",
    "ева",
    "ина",
    "ына",  # Иванова, Пушкина
    "ский",
    "цкий",
    "ской",
    "цкой",
    "ская",
    "цкая",
    "енко",
    "ко",
    "ук",
    "юк",
    "чук",  # Шевченко, Ткачук
    "ян",
    "дзе",
    "швили",
    "оглы",  # Петросян, Гвинашвили
)

# Common given names in the nominative. Needed because the suffix test alone
# cannot separate "Ирина Иванова" — both tokens end in a surname-like "-ина"
# / "-ова". Oblique forms ("Виктора", "Ахмата") are left out on purpose: the
# suffix test already resolves them via the other token.
# Kept as one whitespace-separated block: a name-per-line list would run to
# ~150 lines and bury the logic below it (hence the SIM905 suppression).
_GIVEN_NAMES = frozenset(
    """
    александр алексей анатолий андрей антон аркадий арсений артем артур богдан борис
    вадим валентин валерий василий вениамин виктор виталий владимир владислав всеволод
    вячеслав геннадий георгий глеб григорий давид даниил данил денис дмитрий евгений
    егор ефим захар иван игнат игорь илья иннокентий кирилл константин кузьма лев
    леонид максим марк матвей мирон михаил назар никита николай олег павел петр платон
    прохор роман руслан савелий святослав семен сергей станислав степан тимофей тимур
    трофим федор филипп эдуард эмиль юлиан юрий яков ярослав
    айдар алишер аслан ахмат бекзод зелимхан ибрагим ильдар казбек камиль магомед марат
    рамзан рустам шамиль
    александра алина алла альбина анастасия ангелина анна антонина валентина валерия
    варвара вера вероника виктория галина дарья диана ева евгения екатерина елена
    елизавета жанна зинаида зоя инна ирина карина кристина ксения лариса лидия любовь
    людмила маргарита марина мария надежда наталья нина оксана олеся ольга полина
    раиса регина римма светлана софья таисия тамара татьяна ульяна юлия яна
    """.split()  # noqa: SIM905 - readability: grouped by gender/origin, see comment above
)


def _looks_like_surname(token: str) -> bool:
    return token.endswith(_SURNAME_SUFFIXES)


def _surname_comes_first(first: str, second: str) -> bool:
    """Decide whether ``first`` is the surname in a two-token name.

    Falls back to the registry order ("Фамилия Имя") whenever the two tokens
    give the same signal, so an ambiguous pair is never made worse than the
    previous unconditional behaviour.
    """
    first_is_given = first in _GIVEN_NAMES
    second_is_given = second in _GIVEN_NAMES
    if first_is_given != second_is_given:
        # A recognised given name is the strongest signal available: whichever
        # token is *not* the given name has to be the surname.
        return second_is_given

    first_like = _looks_like_surname(first)
    second_like = _looks_like_surname(second)
    if first_like != second_like:
        return first_like

    return True


def _simple_parse(raw: str, normalized: str, tokens: list[str]) -> NormalizedName:
    """Parse a name with fewer than 3 tokens (no morphological normalization)."""
    if len(tokens) == 2 and not _surname_comes_first(tokens[0], tokens[1]):
        surname, name = tokens[1], tokens[0]
    else:
        surname = tokens[0] if len(tokens) >= 1 else ""
        name = tokens[1] if len(tokens) >= 2 else ""
    patronymic = tokens[2] if len(tokens) >= 3 else ""
    initials = _initials(surname, name, patronymic)

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


def _apply_oblique_table(token: str, table: list[tuple[str, str]], *, min_len: int) -> str | None:
    """Rewrite ``token`` with the first matching rule, or ``None`` if none fits.

    Tables are ordered longest-suffix-first, so the first hit is the most
    specific one. ``min_len`` guards against stripping a short word down to a
    stub — the rewrite is refused rather than applied.
    """
    for oblique, nominative in table:
        if token.endswith(oblique):
            candidate = token[: -len(oblique)] + nominative
            if len(candidate) >= min_len:
                return candidate
    return None


# Which oblique rules apply, by gender. "unknown" tries both, masculine first —
# the same order the three copies of this dispatch used before.
_SURNAME_TABLES: dict[str, list[tuple[str, str]]] = {
    "m": _SURNAME_OBLIQUE_M,
    "f": _SURNAME_OBLIQUE_F,
    "unknown": _SURNAME_OBLIQUE_M + _SURNAME_OBLIQUE_F,
}

_NAME_TABLES: dict[str, list[tuple[str, str]]] = {
    "m": _NAME_OBLIQUE_M,
    "f": _NAME_OBLIQUE_F,
}


def _normalize_surname(surname: str, gender: str) -> str:
    """Normalize surname to nominative."""
    s = surname.lower()
    return _apply_oblique_table(s, _SURNAME_TABLES.get(gender, []), min_len=3) or s


def _normalize_name(name: str, gender: str) -> str:
    """Normalize first name to nominative."""
    n = name.lower()
    return _apply_oblique_table(n, _NAME_TABLES.get(gender, []), min_len=2) or n


def _normalize_patronymic(patronymic: str) -> str:
    """Normalize patronymic to nominative."""
    p = patronymic.lower()
    return _apply_oblique_table(p, _PATRONYMIC_OBLIQUE, min_len=0) or p

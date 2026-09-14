"""Deterministic person name normalization for Entity Resolution v2.

Input is the name as the extraction pipeline stores it (`normalized_data.full_name`,
already reduced to a base case form), so both an incoming mention and stored
persons go through the same rules.

Roles are never assigned from a single suffix: every admissible order of
surname / given name / patronymic becomes a `NameVariant`, and suffixes only
annotate and order them.
"""

from __future__ import annotations

import unicodedata
from itertools import groupby

from persons.resolution.models import (
    NameComponent,
    NameRole,
    NameToken,
    NameVariant,
    NormalizedPersonName,
    RoleSignal,
)

S = NameRole.SURNAME
G = NameRole.GIVEN_NAME
P = NameRole.PATRONYMIC

# Admissible orders of full (non-initial) tokens in Russian-language sources.
_FULL_ORDERS: dict[int, tuple[tuple[NameRole, ...], ...]] = {
    1: ((S,), (G,)),
    2: ((G, S), (S, G), (G, P)),
    3: ((G, P, S), (S, G, P)),
}
# Hints only: "Шостакович" is a surname, "Марина" is a given name. "-вн"/"-ичн" are
# the forms the extraction normalizer leaves after cutting a final "а".
_PATRONYMIC_SUFFIXES = ("ич", "вна", "вн", "ична", "ичн")
_SURNAME_SUFFIXES = (
    "ов", "ев", "ин", "ын", "ова", "ева", "ина", "ына", "ский", "цкий",
    "ская", "цкая", "енко", "ук", "юк", "ян", "швили", "дзе",
)  # fmt: skip


class PersonNameNormalizer:
    def normalize(self, name: str) -> NormalizedPersonName:
        tokens = _tokenize(name)
        return NormalizedPersonName(
            raw=name,
            tokens=tokens,
            canonical_form=" ".join(token.text for token in tokens),
            variants=_variants(tokens),
            block_keys=tuple(sorted({token.text for token in tokens if not token.is_initial})),
        )


def _tokenize(name: str) -> tuple[NameToken, ...]:
    text = unicodedata.normalize("NFKC", name).lower().replace("ё", "е")
    # "И.И.Иванов" → "И. И. Иванов"; any other punctuation separates words.
    cleaned = "".join(char if char.isalpha() or char == "-" else " " for char in text)
    tokens: list[NameToken] = []
    for word in cleaned.split():
        word = "-".join(part for part in word.split("-") if part)
        if word:
            tokens.append(NameToken(text=word, is_initial=len(word) == 1))
    return tuple(tokens)


def _variants(tokens: tuple[NameToken, ...]) -> tuple[NameVariant, ...]:
    orders = _admissible_orders(tokens)
    variants = [_variant(tokens, roles) for roles in orders]
    # Stable: equally plausible readings keep the enumeration order.
    return tuple(sorted(variants, key=lambda variant: -variant.plausibility))


def _admissible_orders(tokens: tuple[NameToken, ...]) -> tuple[tuple[NameRole, ...], ...]:
    shape = tuple((is_initial, len(list(group))) for is_initial, group in groupby(
        tokens, key=lambda token: token.is_initial
    ))  # fmt: skip
    initial_runs = [count for is_initial, count in shape if is_initial]
    if not initial_runs:
        return _FULL_ORDERS.get(len(tokens), ())
    if len(initial_runs) > 1 or initial_runs[0] > 2:
        return ()
    initials = (G, P)[: initial_runs[0]]
    match shape:
        # "И. И. Иванов" / "Иванов И. И."
        case ((True, _), (False, 1)):
            return (initials + (S,),)
        case ((False, 1), (True, _)):
            return ((S,) + initials,)
        # "Иван И. Иванов" / "Иванов Иван И.": the initial is the patronymic.
        case ((False, 1), (True, 1), (False, 1)):
            return ((G, P, S),)
        case ((False, 2), (True, 1)):
            return ((S, G, P),)
    return ()


def _variant(tokens: tuple[NameToken, ...], roles: tuple[NameRole, ...]) -> NameVariant:
    components = tuple(
        NameComponent(role=role, text=token.text, is_initial=token.is_initial)
        for token, role in zip(tokens, roles, strict=True)
    )
    signals: list[RoleSignal] = []
    for part in components:
        if part.is_initial:
            continue
        if part.text.endswith(_PATRONYMIC_SUFFIXES):
            signals.append(
                RoleSignal.PATRONYMIC_SUFFIX
                if part.role is P
                else RoleSignal.PATRONYMIC_SUFFIX_OUTSIDE_PATRONYMIC
            )
        elif part.text.endswith(_SURNAME_SUFFIXES):
            signals.append(
                RoleSignal.SURNAME_SUFFIX
                if part.role is S
                else RoleSignal.SURNAME_SUFFIX_OUTSIDE_SURNAME
            )
    return NameVariant(components=components, signals=tuple(signals))

"""Russian personal names to the nominative case, from a morphological dictionary.

Deterministic and offline (pymorphy3 + its bundled dictionary): the same surface form
always yields the same result. A word the dictionary does not know as a name, surname
or patronymic is left as written — a wrong invented form is worse than an oblique one.
"""

from __future__ import annotations

from functools import lru_cache

from pymorphy3 import MorphAnalyzer
from pymorphy3.analyzer import Parse

# Dictionary marks for the parts of a personal name.
NAME_GRAMMEMES = frozenset({"Name", "Surn", "Patr"})
_GENDERS = ("masc", "femn")
# Surname endings productive enough to decline a name the dictionary does not know, but
# only once another word of the same name has fixed the gender («Михаила Лисина»).
_SURNAME_STEMS = ("ов", "ев", "ёв", "ин", "ын", "ск", "цк")
_MASCULINE_ENDINGS = ("ым", "им", "ом", "ем", "а", "я", "у", "ю", "е")
_FEMININE_ENDINGS = (("ой", "а"), ("ей", "я"), ("ую", "ая"), ("ы", "а"), ("у", "а"), ("е", "а"))


class NameMorphology:
    """Nominative form of a personal name, with one gender for the whole name."""

    def __init__(self, analyzer: MorphAnalyzer | None = None) -> None:
        self._analyzer = analyzer or MorphAnalyzer()

    def to_nominative(self, name: str) -> str:
        words = name.split()
        parses = [self._name_parses(word) for word in words]
        gender, gender_is_certain = self._gender(parses)
        return " ".join(
            word
            if not gender_is_certain and _can_be_nominative(word_parses)
            # «Волкова О. Н.» must not become «Волков О. Н.»: without a gender in the name,
            # a word that can already be nominative stays as written.
            else self._word_to_nominative(word, word_parses, gender)
            for word, word_parses in zip(words, parses, strict=True)
        )

    def is_name_word(self, word: str) -> bool:
        """Whether the dictionary knows the word as a name, a surname or a patronymic."""
        return bool(self._name_parses(word))

    def is_geographic(self, word: str) -> bool:
        """A place name by the dictionary («России», «Калуги»), never a name of its own here."""
        return any("Geox" in parse.tag for parse in self._analyzer.parse(word))

    def can_be_nominative(self, word: str) -> bool:
        """Whether the word can be a singular nominative («София» yes, «Калуги» only plural)."""
        return any(
            "nomn" in parse.tag and "plur" not in parse.tag.grammemes
            for parse in self._analyzer.parse(word)
        )

    def is_adjective(self, word: str) -> bool:
        """An adjective is never part of a person name («Вечная Слава»)."""
        parses = self._analyzer.parse(word)
        return bool(parses) and all("ADJ" in str(parse.tag.POS or "") for parse in parses)

    def is_known(self, word: str) -> bool:
        return any(parse.is_known for parse in self._analyzer.parse(word))

    def is_known_non_name(self, word: str) -> bool:
        """A dictionary word that is never part of a name («Федерации», «Суда», «Танцы»).

        A word the dictionary does not know (a foreign or rare name: «Тирни», «Росавиации»)
        is not judged here — dropping it would lose real people.
        """
        parses = self._analyzer.parse(word)
        return (
            bool(parses) and any(parse.is_known for parse in parses) and not self.is_name_word(word)
        )

    @lru_cache(maxsize=100_000)  # noqa: B019 - one analyzer per normalizer, bounded cache
    def _name_parses(self, word: str) -> tuple[Parse, ...]:
        """Singular name/surname/patronymic parses, best score first."""
        parses = [
            parse
            for parse in self._analyzer.parse(word)
            if NAME_GRAMMEMES & set(parse.tag.grammemes) and "plur" not in parse.tag.grammemes
        ]
        return tuple(sorted(parses, key=lambda parse: (-parse.score, str(parse.tag))))

    @staticmethod
    def _gender(parses: list[tuple[Parse, ...]]) -> tuple[str | None, bool]:
        """The gender of the name and whether a word of the name states it outright.

        «Андрея» is masculine in every reading, so it settles the name. «Волкова» alone is
        either a woman in the nominative or a man in the genitive: the guess from dictionary
        frequencies is reported as uncertain, and the caller then leaves such a word alone.
        """
        for word_parses in parses:
            genders = {
                gender
                for parse in word_parses
                for gender in _GENDERS
                if gender in parse.tag.grammemes
            }
            if len(genders) == 1:
                return genders.pop(), True
        totals = {
            gender: sum(
                max(
                    (parse.score for parse in word_parses if gender in parse.tag.grammemes),
                    default=0.0,
                )
                for word_parses in parses
            )
            for gender in _GENDERS
        }
        # On a tie the feminine reading wins: «задержали Юлию Емельянову» (accusative) is
        # the common sentence, while the masculine dative would invent another person.
        best = max(_GENDERS, key=lambda gender: (totals[gender], gender == "femn"))
        return (best, False) if totals[best] > 0 else (None, False)

    @staticmethod
    def _word_to_nominative(word: str, parses: tuple[Parse, ...], gender: str | None) -> str:
        preferred = [
            parse
            for parse in parses
            if gender is None or gender in parse.tag.grammemes or not _has_gender(parse)
        ]
        for parse in preferred or parses:
            inflected = parse.inflect({"nomn", "sing"}) or parse.inflect({"nomn"})
            if inflected is not None:
                return _capitalize(inflected.word)
        return _undeclined_surname(word, gender) if not parses else word


def _can_be_nominative(parses: tuple[Parse, ...]) -> bool:
    return any("nomn" in parse.tag.grammemes for parse in parses)


def _undeclined_surname(word: str, gender: str | None) -> str:
    """A surname outside the dictionary, declined by its ending once the gender is known."""
    lowered = word.lower()
    if gender == "masc":
        for ending in _MASCULINE_ENDINGS:
            stem = lowered.removesuffix(ending)
            if stem != lowered and stem.endswith(_SURNAME_STEMS):
                return _capitalize(stem)
    elif gender == "femn":
        for ending, replacement in _FEMININE_ENDINGS:
            stem = lowered.removesuffix(ending)
            if stem != lowered and stem.endswith(_SURNAME_STEMS):
                return _capitalize(stem + replacement)
    return word


def _has_gender(parse: Parse) -> bool:
    return bool(set(_GENDERS) & set(parse.tag.grammemes))


def _capitalize(word: str) -> str:
    """«львова-белова» → «Львова-Белова»."""
    return "-".join(part[:1].upper() + part[1:] for part in word.split("-"))

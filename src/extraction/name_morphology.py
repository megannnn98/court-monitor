"""Russian personal names to the nominative case, from a morphological dictionary.

Deterministic and offline (pymorphy3 + its bundled dictionary): the same surface form
always yields the same result. A word the dictionary does not know as a name, surname
or patronymic is left as written — a wrong invented form is worse than an oblique one.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

from pymorphy3 import MorphAnalyzer
from pymorphy3.analyzer import Parse

# Dictionary marks for the parts of a personal name.
NAME_GRAMMEMES = frozenset({"Name", "Surn", "Patr"})
_GENDERS = ("masc", "femn")
# Russian surname endings, checked before the dictionary: feminine first, as «-ова» also
# ends with the masculine «-ов».
_FEMININE_SURNAME_ENDINGS = ("ова", "ева", "ина", "ына", "ская", "цкая", "ая")
_MASCULINE_SURNAME_ENDINGS = ("ов", "ев", "ин", "ын", "ский", "цкий", "ый")
# Surname endings productive enough to decline a name the dictionary does not know, but
# only once another word of the same name has fixed the gender («Михаила Лисина»).
_SURNAME_STEMS = ("ов", "ев", "ёв", "ин", "ын", "ск", "цк")
_ADJECTIVAL_SURNAME_STEMS = ("ск", "цк")
_MASCULINE_ENDINGS = ("ого", "ому", "ым", "им", "ом", "ем", "а", "я", "у", "ю", "е")
_FEMININE_ENDINGS = (("ой", "а"), ("ей", "я"), ("ую", "ая"), ("ы", "а"), ("у", "а"), ("е", "а"))
_FEMININE_A_ENDINGS = (
    ("ы", frozenset({"gent"})),
    ("у", frozenset({"accs"})),
    ("е", frozenset({"datv", "loct"})),
)
_GUESSED_WEIGHT = 0.5
_CASES = frozenset({"nomn", "gent", "datv", "accs", "ablt", "loct"})
_ADJECTIVAL_ENDINGS = ("ого", "ому")
_MASCULINE_ADJECTIVAL_ENDINGS = ("ского", "цкого", "скому", "цкому", "ским", "цким")
_NOT_CONSONANTS = frozenset("аеёиоуыэюяьъй")


class NameMorphology:
    """Nominative form of a personal name, with one gender for the whole name."""

    def __init__(self, analyzer: MorphAnalyzer | None = None) -> None:
        self._analyzer = analyzer or MorphAnalyzer()

    def to_nominative(self, name: str, document_words: Sequence[str] = ()) -> str:
        """`document_words`: other words of the same article, used only to settle the gender."""
        words = name.split()
        parses = [self._name_parses(word) for word in words]
        gender, gender_is_certain = self._gender(parses)
        # «Владлена Татарского»: an adjectival surname outside the dictionary in «-ского»
        # is a man's.
        if not gender_is_certain and any(
            not word_parses and word.lower().endswith(_MASCULINE_ADJECTIVAL_ENDINGS)
            for word, word_parses in zip(words, parses, strict=True)
        ):
            gender, gender_is_certain = "masc", True
        if not gender_is_certain and document_words:
            from_document = self._gender_from_document(parses, document_words)
            if from_document is not None:
                gender, gender_is_certain = from_document, True
        # The case the other words of the name are in («Екатерину» is accusative): a
        # surname outside the dictionary is only declined from an ending of that case.
        cases = {
            grammeme
            for word_parses in parses
            for parse in word_parses
            if parse.is_known and (gender is None or gender in parse.tag.grammemes)
            for grammeme in parse.tag.grammemes
            if grammeme in _CASES
        }
        # «Волкова О. Н.» must not become «Волков О. Н.»: without a gender in the name, a
        # word that can already be nominative stays as written.
        kept = [not gender_is_certain and _can_be_nominative(word_parses) for word_parses in parses]
        declined = [
            word if keep else self._word_to_nominative(word, word_parses, gender, cases)
            for word, word_parses, keep in zip(words, parses, kept, strict=True)
        ]
        # One reading for the whole name, though: once the guessed gender has changed a
        # word, the words kept as written follow it — «Даниила Неонова» is «Даниил Неонов»,
        # not «Даниила Неонов». Initials are never declined, so they change nothing.
        if any(
            not keep and new != word for word, new, keep in zip(words, declined, kept, strict=True)
        ):
            declined = [
                self._word_to_nominative(word, word_parses, gender, cases)
                if keep and not word.endswith(".")
                else new
                for word, word_parses, keep, new in zip(words, parses, kept, declined, strict=True)
            ]
        return " ".join(declined)

    def is_name_word(self, word: str) -> bool:
        """Whether the dictionary knows the word as a name, a surname or a patronymic."""
        return bool(self._name_parses(word))

    def _gender_from_document(
        self, parses: list[tuple[Parse, ...]], document_words: Sequence[str]
    ) -> str | None:
        """The gender of an unambiguous form of the same name elsewhere in the article.

        «Федора Телина» is ambiguous on its own; an article that also writes «Телин» says
        the person is a man.
        """
        wanted = {
            parse.normal_form: gender
            for word_parses in parses
            for parse in word_parses
            for gender in _GENDERS
            if gender in parse.tag.grammemes
        }
        found: set[str] = set()
        for word in document_words:
            # Only a word whose readings of this name agree on the gender says anything:
            # the ambiguous «Телина» (the mention itself) reads as both and settles nothing.
            genders = {
                gender
                for parse in self._name_parses(word)
                if parse.normal_form in wanted
                for gender in _GENDERS
                if gender in parse.tag.grammemes
            }
            if len(genders) == 1:
                found |= genders
        return found.pop() if len(found) == 1 else None

    def is_patronymic(self, word: str) -> bool:
        """A patronymic reading and no given-name one («Александрович» is also a rare
        indeclinable surname in the dictionary, and still a patronymic)."""
        parses = self._name_parses(word)
        return any("Patr" in parse.tag for parse in parses) and not any(
            "Name" in parse.tag for parse in parses
        )

    def is_given_name(self, word: str) -> bool:
        """A given name that is not also a surname («Елену» yes, «Иванова» no)."""
        return self._has_only(word, "Name")

    def _has_only(self, word: str, grammeme: str) -> bool:
        parses = self._name_parses(word)
        return bool(parses) and all(
            grammeme in parse.tag.grammemes
            and not (NAME_GRAMMEMES - {grammeme}) & set(parse.tag.grammemes)
            for parse in parses
        )

    def name_normal_forms(self, word: str, *, fold_feminine: bool = False) -> frozenset[str]:
        """Dictionary base forms of the word as a name («Яроцкого» and «Яроцкий» share one).

        A surname also yields its masculine base form, because the dictionary reads
        «Паклина» as the feminine «паклина» while the article writes «Паклин»; «ё» is
        folded into «е», as the sources spell «Терешин» and the dictionary «Терёшин».
        """
        forms: set[str] = set()
        for parse in self._name_parses(word):
            forms.add(parse.normal_form)
            if "Surn" in parse.tag:
                masculine = parse.inflect({"masc", "sing", "nomn"})
                if masculine is not None:
                    forms.add(masculine.word)
        keys = {form.replace("ё", "е") for form in forms}
        if fold_feminine:
            # «Романа Паклина» is a man in the genitive, but the dictionary reads «Паклина»
            # as a feminine given name: dropping the ending lets both forms meet on one key.
            # Only for a name written in an oblique case — «Анна Иванова» must not fold into
            # «Иванов», who is another person (review finding).
            keys |= {key[:-1] for key in keys if key[-1:] in "ая" and len(key) > 4}
        return frozenset(keys)

    def gender_of(self, name: str) -> str | None:
        """The gender of a person's name, or None when nothing states it.

        A Russian surname ending says it outright and is trusted first: the dictionary
        reads «Паклин» as a feminine indeclinable given name, while «-ин» is masculine.
        """
        for word in name.lower().replace("ё", "е").split():
            if word.endswith(_FEMININE_SURNAME_ENDINGS):
                return "femn"
            if word.endswith(_MASCULINE_SURNAME_ENDINGS):
                return "masc"
        for word in name.split():
            genders = {
                gender
                for parse in self._name_parses(word)
                for gender in _GENDERS
                if gender in parse.tag.grammemes
            }
            if len(genders) == 1:
                return genders.pop()
        return None

    def certain_gender(self, word: str) -> str | None:
        """The gender when every reading of the word as a name agrees on it («Анна»)."""
        genders = {
            gender
            for parse in self._name_parses(word)
            for gender in _GENDERS
            if gender in parse.tag.grammemes
        }
        return genders.pop() if len(genders) == 1 else None

    def surname_gender(self, word: str) -> str | None:
        """Gender by the Russian surname ending («Иванова» feminine, «Иванов» masculine)."""
        lowered = word.lower().replace("ё", "е")
        if lowered.endswith(_FEMININE_SURNAME_ENDINGS):
            return "femn"
        if lowered.endswith(_MASCULINE_SURNAME_ENDINGS):
            return "masc"
        return None

    def is_verb(self, word: str) -> bool:
        """Whether the word can be a verb («задержали» yes, «адвоката» no)."""
        return any(
            str(parse.tag.POS or "") in {"VERB", "INFN", "PRTF", "PRTS", "GRND"}
            for parse in self._analyzer.parse(word)
        )

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
        # A reading the dictionary only guessed («Мониавы» as a masculine name) does not
        # state the gender outright. When two words state opposite genders, the surname
        # wins: «Айшат» is a masculine name in the dictionary, «Кадыровой» a woman's surname.
        certain: dict[str, bool] = {}
        for word_parses in parses:
            if not word_parses or not all(parse.is_known for parse in word_parses):
                continue
            genders = {
                gender
                for parse in word_parses
                for gender in _GENDERS
                if gender in parse.tag.grammemes
            }
            if len(genders) == 1:
                gender = genders.pop()
                certain[gender] = certain.get(gender, False) or _is_surname_only(word_parses)
        if len(certain) == 1:
            return next(iter(certain)), True
        surname_genders = [gender for gender, by_surname in certain.items() if by_surname]
        if len(surname_genders) == 1:
            return surname_genders[0], True
        # All dictionary readings of a word count: «Лидии» is three feminine cases and one
        # masculine. A guessed word («Мониавы») repeats its guesses, so only its best one
        # counts, and for half.
        totals = {
            gender: sum(_gender_weight(word_parses, gender) for word_parses in parses)
            for gender in _GENDERS
        }
        # On a tie the feminine reading wins: «задержали Юлию Емельянову» (accusative) is
        # the common sentence, while the masculine dative would invent another person.
        best = max(_GENDERS, key=lambda gender: (totals[gender], gender == "femn"))
        return (best, False) if totals[best] > 0 else (None, False)

    @staticmethod
    def _word_to_nominative(
        word: str, parses: tuple[Parse, ...], gender: str | None, cases: set[str]
    ) -> str:
        # «Марии Бонцлер»: a woman's name ending in a consonant does not decline («-м» is
        # an instrumental ending: «Балабековым»).
        if gender == "femn" and word[-1:].lower() not in _NOT_CONSONANTS | {"м"}:
            return word
        preferred = [
            parse
            for parse in parses
            if gender is None or gender in parse.tag.grammemes or not _has_gender(parse)
        ]
        for parse in preferred or parses:
            inflected = parse.inflect({"nomn", "sing"}) or parse.inflect({"nomn"})
            if inflected is not None:
                return _capitalize(inflected.word)
        return _undeclined_surname(word, gender, cases) if not parses else word


def _gender_weight(parses: tuple[Parse, ...], gender: str) -> float:
    known: list[float] = [
        parse.score for parse in parses if parse.is_known and gender in parse.tag.grammemes
    ]
    guessed: list[float] = [
        parse.score for parse in parses if not parse.is_known and gender in parse.tag.grammemes
    ]
    return sum(known) + _GUESSED_WEIGHT * max(guessed, default=0.0)


def _is_surname_only(parses: tuple[Parse, ...]) -> bool:
    return all("Surn" in parse.tag and "Name" not in parse.tag for parse in parses)


def _can_be_nominative(parses: tuple[Parse, ...]) -> bool:
    return any("nomn" in parse.tag.grammemes for parse in parses)


def _undeclined_surname(word: str, gender: str | None, cases: set[str]) -> str:
    """A surname outside the dictionary, declined by its ending once the gender is known."""
    lowered = word.lower()
    if gender == "masc":
        for ending in _MASCULINE_ENDINGS:
            stem = lowered.removesuffix(ending)
            if stem == lowered:
                continue
            # «Зарецкого», «Зарецким»: an adjectival surname, «Зарецкий».
            if stem.endswith(_ADJECTIVAL_SURNAME_STEMS):
                return _capitalize(stem + "ий")
            # «Постового» may be «Постовой» or «Постовый»: not guessed.
            if ending in _ADJECTIVAL_ENDINGS:
                continue
            if stem.endswith(_SURNAME_STEMS):
                return _capitalize(stem)
    elif gender == "femn":
        for ending, replacement in _FEMININE_ENDINGS:
            stem = lowered.removesuffix(ending)
            if stem == lowered:
                continue
            if stem.endswith(_ADJECTIVAL_SURNAME_STEMS):
                return _capitalize(stem + "ая")
            if stem.endswith(_SURNAME_STEMS):
                return _capitalize(stem + replacement)
        # «Елене Перепелице», «Юлию Таратуту»: a woman's surname in «-а» written in an
        # oblique case. A surname ending in a consonant or «-о» is indeclinable and stays.
        # «Екатерину Котрикадзе»: an «-е» that is not the case of the name is no ending.
        for ending, ending_cases in _FEMININE_A_ENDINGS:
            stem = lowered.removesuffix(ending)
            if (
                stem != lowered
                and cases & ending_cases
                and len(stem) >= 3
                and stem[-1] not in _NOT_CONSONANTS
            ):
                return _capitalize(stem + "а")
    return word


def _has_gender(parse: Parse) -> bool:
    return bool(set(_GENDERS) & set(parse.tag.grammemes))


def _capitalize(word: str) -> str:
    """«львова-белова» → «Львова-Белова»."""
    return "-".join(part[:1].upper() + part[1:] for part in word.split("-"))

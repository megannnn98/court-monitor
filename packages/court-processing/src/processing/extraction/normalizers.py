from __future__ import annotations

import re
from collections.abc import Sequence
from itertools import takewhile

from processing.extraction.models import (
    NAMED_TITLE_SOURCES,
    EntityType,
    ExtractionDocument,
    LegalReferenceNormalizedData,
    LocationNormalizedData,
    NormalizedMention,
    OrganizationNormalizedData,
    PersonNormalizedData,
    RawMention,
)
from processing.extraction.name_frequency import FEMALE_NAMES, MALE_NAMES
from processing.extraction.name_morphology import NameMorphology

# 1.1.0: personal names are brought to the nominative case with a morphological
# dictionary instead of a suffix table («Ольгу Комлеву» → «Ольга Комлева»).
# 1.2.0: the article settles an ambiguous gender even though it also holds the mention
# itself («Даниила Меркулова» → «Даниил Меркулов»); a guessed or surname-only reading
# does not fix the gender; surnames outside the dictionary are declined by their ending
# («Елене Перепелице», «Антона Зарецкого»); a name with initials is declined too.
# 1.3.0: the part and clause of an article are the ones written before it, so each
# article of an enumeration keeps its own («ст. 275 ч. 2 ст. 205.4 … УК РФ»).
# 1.4.0: the words of a name are read in one gender: once the guessed gender declines a
# word, the ones kept as written follow it («Даниила Неонова» → «Даниил Неонов»).
# 1.5.0: a name after a preposition that takes only the genitive settles the gender of that
# name in the article («для Евгения Поливко» → «Евгений Поливко»); a patronymic states the
# gender before a one-gender dictionary surname («Ипатова Елена Анатольевна»); a registry
# card's name is kept as written.
# 1.6.0: common given-name readings resolve Артём/Лидия only when the lists agree;
# competing readings stay ambiguous and explicit article context takes precedence.
# 1.7.0: the parts of a name (`first_name`, `last_name`, `patronymic`) are told by where
# the patronymic stands and, for two words, by which one is a given name. `full_name`
# and `matching_key` are as before.
# 1.8.0: surname-first initials are stored in their correct fields
# («Бастрыкин А.И.» → surname=Бастрыкин, given initial=А., patronymic initial=И.).
NORMALIZER_VERSION = "1.8.0"
_PATRONYMIC = re.compile(r"(вич|вна|ична|ьич|оглы|кызы)$")
_INITIAL = re.compile(r"[А-ЯЁ]\.")
_SURNAME_INITIALS = re.compile(r"([А-ЯЁ]\.)([А-ЯЁ]\.)?")
# Endings only a Russian surname has, also in an oblique case; «-ин» and «-ич» are left
# out, foreign given names end so too («Мартин», «Колин»).
_NEIGHBOUR_SURNAME = re.compile(
    r"(ов|ев|ова|ева|овой|евой|иной|ского|ской|цкого|цкой|ская|цкая|енко|чук|ых|их)$"
)
# How a Russian surname ends, to tell «Фамилия Имя» from a foreign «Имя Фамилия».
_SURNAME_ENDING = re.compile(
    r"(ов|ев|ин|ын|ова|ева|ина|ына|ский|цкий|ская|цкая|ской|ко|ук|юк|ич|ых|их|ян|дзе|швили)$"
)

_LEGAL_ARTICLE_PATTERN = re.compile(r"(?:ст\.|стать[еяи])\s*(\d+(?:\.\d+)*)", re.IGNORECASE)
_LEGAL_PART_PATTERN = re.compile(r"(?:ч\.|част[ьи])\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_LEGAL_CLAUSE_PATTERN = re.compile(r"(?:п\.|пункт)\s*[«\"]?([а-яa-z])[\"»]?", re.IGNORECASE)
_LEGAL_CODES = (
    ("Уголовного кодекса РФ", "УК РФ"),
    ("Уголовный кодекс РФ", "УК РФ"),
    ("УК РФ", "УК РФ"),
    ("КоАП РФ", "КоАП РФ"),
    ("УК", "УК РФ"),
    ("КоАП", "КоАП РФ"),
    # Spelled out without a country the code may be another state's (Telegram channels of
    # courts in occupied territories cite the Ukrainian code): not guessed. After the short
    # «УК» alias above, which only matches the OVD-Info abbreviation style.
    ("Уголовного кодекса", "УК"),
    ("Уголовный кодекс", "УК"),
)
_LOCATION_ALIASES = {
    "москве": "Москва",
    "москвы": "Москва",
    "казани": "Казань",
    "екатеринбурге": "Екатеринбург",
    "санкт-петербурге": "Санкт-Петербург",
    "новосибирске": "Новосибирск",
}


def _name_words_of(document: ExtractionDocument) -> list[str]:
    """Capitalized words of the article, the context that can settle an ambiguous gender."""
    return _CAPITALIZED_WORDS.findall(document.text)


# Prepositions that take only the genitive: the name after one of them is in that case.
_GENITIVE_PREPOSITIONS = frozenset(
    {"для", "у", "от", "из-за", "без", "против", "после", "вместо", "ради", "кроме", "около"}
)


def _genitive_words_of(document: ExtractionDocument) -> list[str]:
    """Capitalized words right after a preposition that takes only the genitive."""
    return [
        match.group(2)
        for match in _GENITIVE_PHRASE.finditer(document.text)
        if match.group(1).lower() in _GENITIVE_PREPOSITIONS
    ]


_GENITIVE_PHRASE = re.compile(r"(?<![\w-])([А-ЯЁа-яё-]+)\s+([А-ЯЁ][а-яё]{2,})")
_CAPITALIZED_WORDS = re.compile(r"(?<![\w-])[А-ЯЁ][а-яё]{2,}")


def _is_given_name(word: str) -> bool:
    folded = word.lower().replace("ё", "е")
    return folded in MALE_NAMES or folded in FEMALE_NAMES


def _reads_as_given_name(word: str) -> bool:
    """A known given name, or one in an oblique case the dictionary left («Романа»)."""
    folded = word.lower().replace("ё", "е")
    stem = folded[:-1]
    return any(
        form in MALE_NAMES or form in FEMALE_NAMES
        for form in (folded, stem, f"{stem}й", f"{stem}ь", f"{stem}а", f"{stem}я")
    )


def _took_a_neighbours_surname(first: str, second: str) -> bool:
    """«адвоката Ивановой Петра Сидорова»: the span begins with the surname of the
    person named just before. Told only by positive signs: the first word ends as a
    Russian surname does, is no given name itself, and a given name follows it."""
    return (
        bool(_NEIGHBOUR_SURNAME.search(first.lower()))
        and not _is_given_name(first)
        and _reads_as_given_name(second)
    )


def name_parts(words: Sequence[str]) -> tuple[str | None, str | None, str | None]:
    """(given name, surname, patronymic) of a name already in the nominative.

    Entity grouping keys a person by these three, so no word of a name is dropped: a
    word that is neither the given name nor the surname goes to the third field, where
    it keeps two people of one given name and surname apart as a patronymic does.

    - One word is a surname.
    - A surname followed by one or two initials keeps the surname in `last_name`
      and the initials in `first_name` and `patronymic`.
    - Two are «Имя Фамилия»; they are read as «Фамилия Имя» only when the first ends
      as a Russian surname does and the second alone is a known given name.
    - Three with a patronymic in the middle are «Имя Отчество Фамилия». Three ending
      in one are «Фамилия Имя Отчество», when the middle word reads as a given name
      or the first is not one.
    - Three that begin with a neighbour's surname (`_took_a_neighbours_surname`) are
      the last two words: the one case where a word is left out.
    - Any other three, and more, are the first word, the last, and the middle as the
      third field («Джон Пол Смит», «Мамед Гусейн оглы Алиев»).

    Read wrongly, with every word kept: two given names before a surname in «-вич»
    («Иван Давид Абрамович» reads as «Фамилия Имя Отчество»), and two words that are
    both given names («Роман Иван» keeps its order).
    """
    if not words:
        return None, None, None
    # «Бастрыкин А.И.» and «Бастрыкин А. И.» are surname-first, but a
    # known given name followed by an initial («Виталия Л.») is ambiguous.
    # Do not reinterpret the latter as a surname without evidence.
    if len(words) in (2, 3) and not _reads_as_given_name(words[0]):
        if len(words) == 2 and (match := _SURNAME_INITIALS.fullmatch(words[1])):
            return match.group(1), words[0], match.group(2)
        if (
            len(words) == 3
            and _INITIAL.fullmatch(words[1])
            and _INITIAL.fullmatch(words[2])
        ):
            return words[1], words[0], words[2]
    if len(words) == 1:
        return None, words[0], None
    if len(words) == 2:
        first, second = words
        if (
            not _is_given_name(first)
            and _is_given_name(second)
            and _SURNAME_ENDING.search(first.lower())
        ):
            return second, first, None
        return first, second, None
    if len(words) == 3:
        first, second, third = words
        if _PATRONYMIC.search(second.lower()):
            return first, third, second
        if _PATRONYMIC.search(third.lower()) and (
            _reads_as_given_name(second) or not _is_given_name(first)
        ):
            return second, first, third
        if _took_a_neighbours_surname(first, second):
            return second, third, None
    return words[0], words[-1], " ".join(words[1:-1])


class RuleBasedMentionNormalizer:
    normalizer_version = NORMALIZER_VERSION

    def __init__(self, name_morphology: NameMorphology | None = None) -> None:
        self._name_morphology = name_morphology or NameMorphology()

    def supports(self, entity_type: EntityType) -> bool:
        return entity_type in set(EntityType)

    def normalize(
        self,
        mention: RawMention,
        document: ExtractionDocument,
    ) -> NormalizedMention:
        normalized_data: (
            PersonNormalizedData
            | LegalReferenceNormalizedData
            | OrganizationNormalizedData
            | LocationNormalizedData
        )
        if (
            mention.entity_type is EntityType.PERSON
            and document.source_name in NAMED_TITLE_SOURCES
            and mention.surface_text == document.title
        ):
            normalized_text, normalized_data = self._registry_name(mention.surface_text)
        elif mention.entity_type is EntityType.PERSON:
            normalized_text, normalized_data = self._normalize_person(
                mention.surface_text,
                _name_words_of(document),
                _genitive_words_of(document),
            )
        elif mention.entity_type is EntityType.LEGAL_REFERENCE:
            normalized_text, normalized_data = self._normalize_legal_reference(mention.surface_text)
        elif mention.entity_type is EntityType.COURT:
            normalized_text, normalized_data = self._normalize_organization(
                mention.surface_text,
                "court",
            )
        elif mention.entity_type is EntityType.ORGANIZATION:
            normalized_text, normalized_data = self._normalize_organization(
                mention.surface_text,
                self._organization_type(mention.surface_text),
            )
        else:
            normalized_text, normalized_data = self._normalize_location(mention.surface_text)

        return NormalizedMention(
            entity_type=mention.entity_type,
            surface_text=mention.surface_text,
            normalized_text=normalized_text,
            start_offset=mention.start_offset,
            end_offset=mention.end_offset,
            confidence=mention.confidence,
            normalized_data=normalized_data,
            extractor_name=mention.extractor_name,
            extractor_version=mention.extractor_version,
            normalizer_version=self.normalizer_version,
        )

    def _registry_name(self, name: str) -> tuple[str, PersonNormalizedData]:
        """A registry's «Фамилия Имя Отчество», already nominative: kept as written."""
        normalized = " ".join(name.replace("ё", "е").replace("Ё", "Е").split())
        words = normalized.split()
        return normalized, PersonNormalizedData(
            full_name=normalized,
            last_name=words[0],
            first_name=words[1] if len(words) >= 2 else None,
            patronymic=words[2] if len(words) >= 3 else None,
            matching_key=self._matching_key(normalized),
        )

    def normalize_person(self, surface_text: str) -> tuple[str, PersonNormalizedData]:
        """Person name as the pipeline stores it (used by ER v2 dry-run/evaluation)."""
        return self._normalize_person(surface_text)

    def _normalize_person(
        self,
        surface_text: str,
        document_words: Sequence[str] = (),
        genitive_words: Sequence[str] = (),
    ) -> tuple[str, PersonNormalizedData]:
        words = surface_text.replace("ё", "е").replace("Ё", "Е").split()
        if words and "." in words[0]:
            # «Е.А. Аничкиной»: the initials stay, the surname is brought to the nominative.
            initials = list(takewhile(lambda word: "." in word, words))
            surname = words[len(initials) :]
            if surname:
                surname = (
                    self._name_morphology.to_nominative(
                        " ".join(surname), document_words, genitive_words
                    )
                    .replace("ё", "е")
                    .replace("Ё", "Е")
                    .split()
                )
            normalized = " ".join([*initials, *surname])
            matching_key = self._matching_key(normalized)
            return normalized, PersonNormalizedData(
                full_name=normalized,
                last_name=(surname or words)[-1],
                first_name=None,
                patronymic=None,
                matching_key=matching_key,
            )

        # The dictionary writes «ё»; stored names and matching keys use «е» throughout.
        normalized_words = (
            self._name_morphology.to_nominative(" ".join(words), document_words, genitive_words)
            .replace("ё", "е")
            .replace("Ё", "Е")
            .split()
        )
        normalized = " ".join(normalized_words)
        first_name, last_name, patronymic = name_parts(normalized_words)
        return normalized, PersonNormalizedData(
            full_name=normalized,
            last_name=last_name,
            first_name=first_name,
            patronymic=patronymic,
            matching_key=self._matching_key(normalized),
        )

    def _normalize_legal_reference(
        self,
        surface_text: str,
    ) -> tuple[str, LegalReferenceNormalizedData]:
        code = next(
            normalized
            for alias, normalized in _LEGAL_CODES
            if alias.lower() in surface_text.lower()
        )
        article_match = _LEGAL_ARTICLE_PATTERN.search(surface_text)
        # The part and clause of an article are written before it. Only a single
        # reference may have them after it («ст. 282 ч. 2 УК»): in an enumeration
        # («ст. 275 ч. 2 ст. 205.4 … УК РФ») the part after belongs to the next article.
        qualifiers = surface_text
        if article_match is not None:
            before = surface_text[: article_match.start()]
            single = len(_LEGAL_ARTICLE_PATTERN.findall(surface_text)) == 1
            qualifiers = surface_text if single and not before.strip() else before
        part_match = _LEGAL_PART_PATTERN.search(qualifiers)
        clause_match = _LEGAL_CLAUSE_PATTERN.search(qualifiers)
        data = LegalReferenceNormalizedData(
            code=code,
            article=article_match.group(1) if article_match else None,
            part=part_match.group(1) if part_match else None,
            clause=clause_match.group(1).lower() if clause_match else None,
        )
        parts = [code]
        if data.article is not None:
            parts.append(f"ст. {data.article}")
        if data.part is not None:
            parts.append(f"ч. {data.part}")
        if data.clause is not None:
            parts.append(f"п. {data.clause}")
        return " ".join(parts), data

    def _normalize_organization(
        self,
        surface_text: str,
        organization_type: str,
    ) -> tuple[str, OrganizationNormalizedData]:
        name = " ".join(surface_text.split())
        data = OrganizationNormalizedData(
            name=name,
            organization_type=organization_type,
            location=self._extract_location_from_name(name),
            matching_key=self._matching_key(name),
        )
        return name, data

    def _normalize_location(self, surface_text: str) -> tuple[str, LocationNormalizedData]:
        lowered = surface_text.lower()
        name = _LOCATION_ALIASES.get(lowered, surface_text)
        data = LocationNormalizedData(
            name=name,
            location_type=None,
            matching_key=self._matching_key(name),
        )
        return name, data

    @staticmethod
    def _matching_key(value: str) -> str:
        return re.sub(r"[^а-яa-z0-9]+", "", value.lower().replace("ё", "е"))

    @staticmethod
    def _organization_type(surface_text: str) -> str:
        lowered = surface_text.lower()
        if lowered in {"мвд", "фсб", "фсин", "ск рф"} or "комитет" in lowered:
            return "state_body"
        if "полици" in lowered or "прокуратур" in lowered:
            return "law_enforcement"
        if surface_text in {"SOTA", "Медиазона"}:
            return "media"
        return "organization"

    @staticmethod
    def _extract_location_from_name(name: str) -> str | None:
        for marker in ("Москвы", "Москве", "Москва", "Петербурга", "Казани"):
            if marker in name:
                return _LOCATION_ALIASES.get(marker.lower(), marker)
        return None

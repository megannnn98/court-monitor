from __future__ import annotations

import re
from collections.abc import Iterable

from extraction.models import EntityType, ExtractionDocument, RawMention
from extraction.name_morphology import NameMorphology

_LEGAL_CODE = r"(?:УК\s+РФ|КоАП\s+РФ|Уголовного\s+кодекса\s+РФ|Уголовный\s+кодекс\s+РФ)"
# After an article number the code is usually written without «РФ»: «ст. 207.3 УК».
_LEGAL_CODE_AFTER_ARTICLE = (
    r"(?:(?:УК|КоАП|Уголовного\s+кодекса|Уголовный\s+кодекс)(?:\s+РФ)?)(?![А-Яа-яЁёA-Za-z])"
)
_LEGAL_REFERENCE_PATTERN = re.compile(
    rf"""
    (?:
        (?:(?:п\.|пункт)\s*[«"]?(?P<clause>[а-яa-z])["»]?\s*)?
        (?:(?:ч\.|част[ьи])\s*(?P<part>\d+(?:\.\d+)?)\s*)?
        (?:(?:ст\.|стать[еяи])\s*(?P<article>\d+(?:\.\d+)*)\s*)?
        (?P<code>{_LEGAL_CODE})
    )
    |
    (?:
        (?:(?:п\.|пункт)\s*[«"]?(?P<clause2>[а-яa-z])["»]?\s*)?
        (?:(?:ч\.|част[ьи])\s*(?P<part2>\d+(?:\.\d+)?)\s*)?
        (?:ст\.|стать[еяи])\s*(?P<article2>\d+(?:\.\d+)*)(?:\s*и\s*\d+(?:\.\d+)*)?\s*
        (?P<code2>{_LEGAL_CODE_AFTER_ARTICLE})
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_CAPITALIZED_WORD = r"(?:[А-ЯЁA-Z][а-яёa-z]+(?:-[А-ЯЁA-Z][а-яёa-z]+)?)"
_COURT_PREFIX_WORD = r"(?:[А-ЯЁA-Z][а-яёa-z]+|[а-яё]+)"
_INITIALS = r"(?:[А-ЯЁA-Z]\.\s*){1,2}"
_PERSON_PATTERNS = [
    re.compile(rf"\b{_CAPITALIZED_WORD}\s+{_CAPITALIZED_WORD}\s+{_CAPITALIZED_WORD}\b"),
    re.compile(rf"\b{_CAPITALIZED_WORD}\s+{_CAPITALIZED_WORD}\b"),
    re.compile(rf"\b{_INITIALS}{_CAPITALIZED_WORD}\b"),
    # «Виталия Л.»: a given name with the surname reduced to an initial (OVD-Info style).
    re.compile(rf"\b{_CAPITALIZED_WORD}\s+[А-ЯЁ]\.(?![А-Яа-яЁё])"),
]
# Words that may start a capitalized two-word match without being part of the name:
# sentence adverbs/conjunctions and role or occupation descriptors (by stem).
_LEADING_NON_NAME_WORDS = frozenset(
    {
        "позже",
        "позднее",
        "также",
        "однако",
        "теперь",
        "ранее",
        "сейчас",
        "затем",
        "кроме",
        "вместе",
        "после",
        "при",
        "как",
        "по",
        "еще",
        "ещё",
        "сегодня",
        "вчера",
        "и",
        "а",
        "но",
        "его",
        "ее",
        "её",
        "их",
        "против",
        "для",
        "от",
        "про",
        "об",
    }
)
_LEADING_ROLE_STEMS = (
    "судь",
    "следовател",
    "прокурор",
    "дознавател",
    "адвокат",
    "защитни",
    "правозащитн",
    "журналист",
    "активист",
    "блогер",
    "политолог",
    "художни",
    "врач",
    "начальни",
    "председател",
    "представител",
    "эксперт",
    "юрист",
    "депутат",
)
_PATRONYMIC_SUFFIXES = (
    "вич",
    "вна",
    "вны",
    "вне",
    "вну",
    "вной",
    "вича",
    "вичу",
    "вичем",
    "ична",
    "ичны",
    "ичне",
    "ичну",
)
_CASE_ENDINGS = (
    "ыми",
    "ими",
    "ого",
    "его",
    "ому",
    "ему",
    "ой",
    "ей",
    "ою",
    "ею",
    "ую",
    "юю",
    "ым",
    "им",
    "ом",
    "ем",
    "ах",
    "ях",
    "а",
    "я",
    "у",
    "ю",
    "е",
    "ы",
    "и",
)
_SINGLE_CAPITALIZED = re.compile(r"(?<![\w-])[А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁ][а-яё]+)?(?![\w-])")
_PERSON_STOP_WORDS = {
    "Басманный районный",
    "Московский городской",
    "Следственный комитет",
    "Уголовного кодекса",
    "Верховный суд",
    "Европейский суд",
}

_COURT_PATTERN = re.compile(
    rf"\b(?:{_COURT_PREFIX_WORD}\s+){{0,4}}(?:суд|суда|суде|судом)"
    rf"(?:\s+(?:Москвы|Петербурга|России|Татарстана|{_CAPITALIZED_WORD}))?",
)
_ORG_PATTERN = re.compile(
    r"\b(?:ОВД-Инфо|SOTA|Медиазон[аеуы]|Роскомсвобод[аеуы]|Мемориал|Следственный комитет|"
    r"СК РФ|МВД|ФСБ|ФСИН|прокуратур[аеуы]|Генпрокуратур[аеуы]|Минюст|полици[яиюей]|"
    # Case endings too: «охранника Минюста Виталия» must not read as a name.
    r"Комитет против пыток|Росфинмониторинг)(?:[а-яё]{1,2})?\b",
    re.IGNORECASE,
)
_LOCATION_PATTERN = re.compile(
    r"\b(?:Россия|Беларусь|Украина|Москва|Москве|Москвы|Санкт-Петербург|Петербург|"
    r"Санкт-Петербурге|Казань|Казани|Екатеринбург|Екатеринбурге|Новосибирск|Новосибирске|"
    r"Татарстан|Дагестан|Чечня|"
    r"Краснодарский край|Московская область|Ленинградская область)\b"
)


def _starts_a_sentence(text: str, start: int) -> bool:
    """A capital letter at the start of a sentence says nothing about the word being a name."""
    before = text[:start].rstrip()
    return not before or before[-1] in ".!?:;»\n"


def _trim_leading_non_name(text: str, start: int, end: int, morphology: NameMorphology) -> int:
    """Skip leading words that are not part of the name.

    Sentence words and role descriptors by their own lists, plus a place name written
    before the name itself: «России Мария Захарова», «Калуги Ивана Любшина».
    """
    while True:
        word = re.match(r"\S+\s+", text[start:end])
        if word is None:
            return start
        surface = word.group(0).strip()
        lowered = surface.lower()
        if lowered in _LEADING_NON_NAME_WORDS or lowered.startswith(_LEADING_ROLE_STEMS):
            start += word.end()
            continue
        rest = text[start + word.end() : end].split()
        # A name does not start with a word that has no nominative reading: «Калуги Ивана
        # Любшина», «Задержали Ивана». «София Чепик» keeps its first word — it is nominative.
        unknown_before_full_name = (
            # «Сколтеха Даниила Меркулова»: an unknown word before a name of its own is not
            # part of it; with one name word left it may be a foreign given name («Ремзи»).
            not morphology.is_known(surface)
            and len(rest) >= 2
            and all(morphology.is_name_word(other) for other in rest)
        )
        if (
            rest
            and not morphology.is_name_word(surface)
            and any(morphology.is_name_word(other) for other in rest)
            and (
                unknown_before_full_name
                or (
                    morphology.is_known_non_name(surface)
                    and (
                        not morphology.can_be_nominative(surface)
                        or morphology.is_adjective(surface)
                        # «Приговор Ремзи Куртнезирову»: the capital opens the sentence.
                        or _starts_a_sentence(text, start)
                    )
                )
            )
        ):
            start += word.end()
            continue
        return start


def _is_not_a_person(surface: str, morphology: NameMorphology) -> bool:
    """No word is a name and at least one is an ordinary dictionary word.

    «Российской Федерации», «Танцы Минус», «Глава Росавиации». A span whose words the
    dictionary does not know at all may be a foreign name («Джейкоб Тирни») and is kept.
    """
    words = surface.split()
    if not words or any(morphology.is_name_word(word) for word in words):
        return False
    return any(morphology.is_known_non_name(word) for word in words)


def _word_stem(word: str) -> str:
    lowered = word.lower().replace("ё", "е")
    for ending in _CASE_ENDINGS:
        if lowered.endswith(ending) and len(lowered) - len(ending) >= 4:
            return lowered[: -len(ending)]
    return lowered


def _surname_stem(name: str) -> str | None:
    tokens = [token for token in name.split() if not token.endswith(".")]
    if len(tokens) < 2 and not re.search(_INITIALS, name):
        return None
    if not tokens:
        return None
    surname = tokens[-1]
    if len(tokens) == 3 and tokens[-1].lower().endswith(_PATRONYMIC_SUFFIXES):
        surname = tokens[0]
    stem = _word_stem(surname)
    return stem if len(stem) >= 4 else None


class RuleBasedEntityExtractor:
    extractor_name = "rule-based-entity-extractor"
    # 1.1.0: surname-only references to a full name in the same article; leading role
    # and sentence words trimmed from names; overlapping name spans keep the longest.
    # 1.1.1: inflected organization names («Минюста», «Медиазоны») occupy their span.
    # 1.2.0: a word before the name is trimmed and a span of ordinary dictionary words is
    # not a person, both decided by the morphological dictionary.
    extractor_version = "1.2.0"

    def __init__(self, morphology: NameMorphology | None = None) -> None:
        self._morphology = morphology or NameMorphology()

    def extract(self, document: ExtractionDocument) -> list[RawMention]:
        mentions: list[RawMention] = []
        mentions.extend(self._legal_references(document.text))
        mentions.extend(self._courts(document.text))
        mentions.extend(self._organizations(document.text))
        mentions.extend(self._locations(document.text))
        occupied = [(mention.start_offset, mention.end_offset) for mention in mentions]
        mentions.extend(self._people(document.text, occupied))
        return sorted(
            self._deduplicate(mentions),
            key=lambda mention: (
                mention.start_offset,
                mention.end_offset,
                mention.entity_type.value,
            ),
        )

    def _legal_references(self, text: str) -> list[RawMention]:
        return [
            self._mention(EntityType.LEGAL_REFERENCE, text, match.start(), match.end(), 0.95)
            for match in _LEGAL_REFERENCE_PATTERN.finditer(text)
            if match.group(0).strip()
        ]

    def _courts(self, text: str) -> list[RawMention]:
        return [
            self._mention(EntityType.COURT, text, match.start(), match.end(), 0.88)
            for match in _COURT_PATTERN.finditer(text)
            if self._is_specific_court(match.group(0))
        ]

    def _organizations(self, text: str) -> list[RawMention]:
        return [
            self._mention(EntityType.ORGANIZATION, text, match.start(), match.end(), 0.82)
            for match in _ORG_PATTERN.finditer(text)
        ]

    def _locations(self, text: str) -> list[RawMention]:
        return [
            self._mention(EntityType.LOCATION, text, match.start(), match.end(), 0.78)
            for match in _LOCATION_PATTERN.finditer(text)
        ]

    def _people(
        self,
        text: str,
        occupied: Iterable[tuple[int, int]],
    ) -> list[RawMention]:
        occupied_spans = list(occupied)
        spans: list[tuple[int, int]] = []
        for pattern in _PERSON_PATTERNS:
            for match in pattern.finditer(text):
                start = _trim_leading_non_name(text, match.start(), match.end(), self._morphology)
                surface = text[start : match.end()]
                if len(surface.split()) < 2 and not re.search(_INITIALS, surface):
                    continue
                # «Skandi Klubb», «Frankfurter Allgemeine Zeitung»: people are written in
                # Cyrillic in these sources, Latin spans are outlets, bands and venues.
                if not re.search(r"[А-ЯЁа-яё]", surface):
                    continue
                if self._is_person_stop_word(surface) or _is_not_a_person(
                    surface, self._morphology
                ):
                    continue
                if self._overlaps(start, match.end(), occupied_spans):
                    continue
                spans.append((start, match.end()))
        # Overlapping readings of one name («Ольга Иванова» and «Ольга Иванова Петровна»):
        # keep the longest.
        kept: list[tuple[int, int]] = []
        for start, end in sorted(set(spans), key=lambda span: (span[0] - span[1], span[0])):
            if not self._overlaps(start, end, kept):
                kept.append((start, end))
        mentions = [
            self._mention(EntityType.PERSON, text, start, end, 0.72) for start, end in sorted(kept)
        ]
        mentions.extend(self._surname_references(text, kept, occupied_spans))
        return mentions

    def _surname_references(
        self,
        text: str,
        names: list[tuple[int, int]],
        occupied: list[tuple[int, int]],
    ) -> list[RawMention]:
        """A surname alone that repeats the surname of a full name in the same article.

        Only surnames already named in full here are recognised: a capitalized word
        alone is never a person by itself.
        """
        stems = {
            stem for start, end in names if (stem := _surname_stem(text[start:end])) is not None
        }
        if not stems:
            return []
        references = []
        taken = [*names, *occupied]
        for match in _SINGLE_CAPITALIZED.finditer(text):
            if self._overlaps(match.start(), match.end(), taken):
                continue
            if _word_stem(match.group(0)) in stems:
                references.append(
                    self._mention(EntityType.PERSON, text, match.start(), match.end(), 0.6)
                )
        return references

    @staticmethod
    def _is_person_stop_word(surface: str) -> bool:
        if surface in _PERSON_STOP_WORDS:
            return True
        lowered = surface.lower()
        return any(
            re.search(rf"\b{marker}\b", lowered) is not None
            for marker in ("суд", "кодекс", "область", "край", "республика")
        )

    @staticmethod
    def _is_specific_court(surface: str) -> bool:
        lowered = surface.lower()
        if "суд" not in lowered:
            return False
        if lowered in {"суд", "позже суд"}:
            return False
        if lowered.endswith(" суд") and not any(
            marker in lowered
            for marker in ("районный", "городской", "областной", "верховный", "басманный")
        ):
            return False
        return not (lowered.startswith("в ") and lowered.endswith(" суд"))

    @staticmethod
    def _overlaps(
        start: int,
        end: int,
        spans: Iterable[tuple[int, int]],
    ) -> bool:
        return any(start < span_end and end > span_start for span_start, span_end in spans)

    def _mention(
        self,
        entity_type: EntityType,
        text: str,
        start: int,
        end: int,
        confidence: float,
    ) -> RawMention:
        return RawMention(
            entity_type=entity_type,
            surface_text=text[start:end],
            start_offset=start,
            end_offset=end,
            confidence=confidence,
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
        )

    @staticmethod
    def _deduplicate(mentions: Iterable[RawMention]) -> list[RawMention]:
        seen: set[tuple[EntityType, int, int, str]] = set()
        result: list[RawMention] = []
        for mention in mentions:
            key = (
                mention.entity_type,
                mention.start_offset,
                mention.end_offset,
                mention.surface_text,
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(mention)
        return result

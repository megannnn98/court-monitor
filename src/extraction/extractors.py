from __future__ import annotations

import re
from collections.abc import Iterable
from itertools import pairwise

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
# Matched overlapping (a lookahead around each): «Павла Крисевича Елену Иванову» holds two
# people, and a non-overlapping scan would consume the first word of the second one.
_PERSON_PATTERNS = [
    re.compile(rf"(?=(\b{_CAPITALIZED_WORD}\s+{_CAPITALIZED_WORD}\s+{_CAPITALIZED_WORD}\b))"),
    re.compile(rf"(?=(\b{_CAPITALIZED_WORD}\s+{_CAPITALIZED_WORD}\b))"),
    re.compile(rf"(?=(\b{_INITIALS}{_CAPITALIZED_WORD}\b))"),
    # «Виталия Л.»: a given name with the surname reduced to an initial (OVD-Info style).
    re.compile(rf"(?=(\b{_CAPITALIZED_WORD}\s+[А-ЯЁ]\.(?![А-Яа-яЁё])))"),
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


def _trim_second_person(text: str, start: int, end: int, morphology: NameMorphology) -> int:
    """Cut the span where another person starts: a given name cannot follow a patronymic.

    «Павла Крисевича Елену» is two people; «Мифтахова Азата Фанисовича» is one.
    """
    words = list(re.finditer(r"\S+", text[start:end]))
    for previous, word in pairwise(words):
        if morphology.is_patronymic(previous.group(0)) and morphology.is_given_name(word.group(0)):
            return start + previous.end()
    return end


def _capitalized_run(text: str, start: int, end: int) -> list[str]:
    """The uninterrupted run of capitalized words this span belongs to."""
    left = start
    while (previous := re.search(rf"({_CAPITALIZED_WORD})\s+$", text[:left])) is not None:
        left = previous.start(1)
    right = end
    while (following := re.match(rf"\s+({_CAPITALIZED_WORD})", text[right:])) is not None:
        right += following.end(1)
    return text[left:right].split()


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


def _surname_word(name: str) -> str | None:
    """The surname of a full name: last word, or first when a patronymic closes the name."""
    tokens = [token for token in name.split() if not token.endswith(".")]
    if len(tokens) < 2 and not re.search(_INITIALS, name):
        return None
    if not tokens:
        return None
    if len(tokens) == 3 and tokens[-1].lower().endswith(_PATRONYMIC_SUFFIXES):
        return tokens[0]
    return tokens[-1]


class RuleBasedEntityExtractor:
    extractor_name = "rule-based-entity-extractor"
    # 1.1.0: surname-only references to a full name in the same article; leading role
    # and sentence words trimmed from names; overlapping name spans keep the longest.
    # 1.1.1: inflected organization names («Минюста», «Медиазоны») occupy their span.
    # 1.2.0: a word before the name is trimmed and a span of ordinary dictionary words is
    # not a person, both decided by the morphological dictionary.
    # 1.3.0: overlapping name spans prefer more name words over more characters.
    extractor_version = "1.3.0"

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
                match_end = match.end(1)
                start = _trim_leading_non_name(text, match.start(1), match_end, self._morphology)
                span_end = _trim_second_person(text, start, match_end, self._morphology)
                surface = text[start:span_end]
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
                if self._overlaps(start, span_end, occupied_spans):
                    continue
                if not self._is_plausible_full_name(text, start, span_end):
                    continue
                spans.append((start, span_end))

        # Overlapping readings of one name («Ольга Иванова» and «Ольга Иванова Петровна»):
        # keep the one with the most name words, then the longest. By length alone
        # «Popcorn Books Дмитрия» beat «Дмитрия Протопопова» and the surname was lost.
        def preference(span: tuple[int, int]) -> tuple[int, int, int]:
            name_words = sum(
                self._morphology.is_name_word(word) for word in text[span[0] : span[1]].split()
            )
            return (-name_words, span[0] - span[1], span[0])

        kept: list[tuple[int, int]] = []
        for start, end in sorted(set(spans), key=preference):
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
        # A surname key per required gender. The dictionary base form of «Иванова» is the
        # masculine «иванов», so a name written in the nominative only matches a repeat of
        # the same gender: «Анна Иванова» and «Иванов» are two people (review finding). A
        # name in an oblique case («Романа Паклина») carries a case ending, not a feminine
        # one, so its key is folded and matches any gender.
        keys: dict[str | None, set[str]] = {}
        for start, end in names:
            full_name = text[start:end]
            surname = _surname_word(full_name)
            if surname is None:
                continue
            oblique = any(
                not self._morphology.can_be_nominative(word) for word in full_name.split()
            )
            # Only a given name that states its gender outright («Анна Иванова») requires
            # the repeat to agree: «Романа Паклина» reads as either gender, and demanding
            # agreement there cost 16 mentions on the validation corpus.
            words = full_name.split()
            gender = None if oblique else self._morphology.certain_gender(words[0])
            # «Романа Паклина»: unless the given name says the person is a woman, a final
            # «а» is a case ending and the key is folded; for «Анна Иванова» the gender
            # above blocks a repeat written as «Иванов».
            forms = self._morphology.name_normal_forms(surname, fold_feminine=gender != "femn") or {
                _word_stem(surname)
            }
            keys.setdefault(gender, set()).update(forms)
        if not keys:
            return []
        references = []
        taken = [*names, *occupied]
        for match in _SINGLE_CAPITALIZED.finditer(text):
            if self._overlaps(match.start(), match.end(), taken):
                continue
            word = match.group(0)
            forms = self._morphology.name_normal_forms(
                word, fold_feminine=not self._morphology.can_be_nominative(word)
            ) or {_word_stem(word)}
            # Only one direction is blocked: a woman's surname does not repeat as a
            # masculine form («Анна Иванова» and «Иванов» are two people). The reverse is
            # normal Russian: «Андрея Акузина» is a man's surname in the genitive, whose
            # ending looks feminine.
            masculine_form = self._morphology.surname_gender(word) == "masc"
            allowed = {
                key
                for gender, gender_keys in keys.items()
                for key in gender_keys
                if not (gender == "femn" and masculine_form)
            }
            if forms & allowed:
                references.append(
                    self._mention(EntityType.PERSON, text, match.start(), match.end(), 0.6)
                )
        return references

    def _is_plausible_full_name(self, text: str, start: int, end: int) -> bool:
        """Reject a three-word span that cuts across a list of people.

        «Иван Петров Сергей Сидоров» is four capitalized words in a row with no
        patronymic: two names, not one (review finding). A three-word name standing on its
        own is kept even without a patronymic («Дон Виктор Кирман»), because requiring one
        measured worse: duplicate persons 1 -> 2 on the validation corpus.
        """
        words = text[start:end].split()
        if len(words) < 3 or any(self._morphology.is_patronymic(word) for word in words):
            return True
        # Only name words count: «Задержали Дон Виктор Кирман» is one name after a verb.
        run = [
            word
            for word in _capitalized_run(text, start, end)
            if self._morphology.is_name_word(word)
        ]
        return len(run) < 4

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

from __future__ import annotations

import re
from datetime import UTC, datetime
from itertools import pairwise

from processing.extraction.models import (
    EntityType,
    EventEntityLink,
    EventEntityRole,
    EventMention,
    EventType,
    ExtractionDocument,
    NormalizedMention,
)
from processing.extraction.name_morphology import NameMorphology

_ABBREVIATIONS = ("ст.", "ч.", "п.")

# A person introduced by a procedural or professional role takes part in the event
# without being its subject («судья Нефедов арестовал…», «его адвокат рассказал…»).
NON_SUBJECT_ROLES = (
    "судья",
    "судьи",
    "следователь",
    "следовательница",
    "прокурор",
    "прокурора",
    "дознаватель",
    "дознавательница",
    "адвокат",
    "адвоката",
    "адвокатка",
    "защитник",
    "защитника",
    "защитница",
    "защитницы",
    "врач",
    "начальник",
    "начальница",
    "представитель",
    "представительница",
    "председатель",
    "эксперт",
    "экспертка",
    "юрист",
    "юристка",
)
_ROLE_BEFORE_NAME = re.compile(
    r"(?:^|[\s«(])(?:" + "|".join(NON_SUBJECT_ROLES) + r")\s+(?:[а-яё-]+\s+){0,2}$",
    re.IGNORECASE,
)
_ROLE_AS_FIRST_TOKEN = re.compile(r"^(?:" + "|".join(NON_SUBJECT_ROLES) + r")\s", re.IGNORECASE)
# The person reports the event rather than undergoes it.
_SOURCE_BEFORE_NAME = re.compile(
    r"(?:по данным|по словам|со слов|как сообщил[аи]?)\s+(?:[а-яё-]+\s+){0,3}$", re.IGNORECASE
)
_SPEECH_AFTER_NAME = re.compile(
    r"^[\s,»\")]*(?:[а-яё-]+\s+){0,1}(?:сообщил[аи]?|рассказал[аи]?|заявил[аи]?|"
    r"отметил[аи]?|добавил[аи]?|писал[аи]?|пишет|пишут|говорит|считает|уточнил[аи]?)\b",
    re.IGNORECASE,
)
_CONTEXT_CHARS = 60
# The person a crime was aimed at: «подготовке убийства бывшего республиканского министра
# курортов и туризма Вадима Волченко». Only lowercase words may stand between.
_VICTIM_BEFORE_NAME = re.compile(
    r"(?<![а-яё])(?:убийств[аеоу]?|покушени[еяи]\s+на|нападени[еяи]\s+на|похищени[еяи])"
    r"\s+(?:[а-яё-]+\s+){0,7}$"
)
_VICTIM_CONTEXT_CHARS = 120

# Triggers match at a word start. Verb (or verb-like) triggers state that the event
# happened in this sentence; noun triggers («приговора», «ареста») often only refer to
# another event, so they count only when no verb trigger is present.
_W = r"(?<![а-яё])"
_VERB_TRIGGERS: tuple[tuple[EventType, str], ...] = (
    (EventType.CASE_OPENED, _W + r"(?:возбу[дж]\w*|завел[аио]?\s+(?:\w+\s+){0,2}дел\w*)"),
    (EventType.SEARCH, _W + r"(?:обыск\w*)"),
    # Past verbs and short participles only: «задержанные», «к задержанным» are people.
    (EventType.DETENTION, _W + r"(?:задерж(?:ал|али|ала|ало|ивали|ан|ана|аны|ано)(?![а-яё]))"),
    (
        EventType.ARREST,
        _W + r"(?:арестова\w*|(?:заключ|взя|помести)\w*\s+под\s+страж\w*|"
        # «отправили в СИЗО», «оказался в СИЗО», «помещён в следственный изолятор» — but
        # «отправили письма в СИЗО» is support, «направил запрос в СИЗО» a request.
        r"(?:отправ(?:ил|ля|лен)\w*|помести\w*|помещ[её]н\w*|оказал\w*)\s+"
        r"(?:(?!письм|открытк|посылк|передач|запрос|жалоб|ходатайств|деньг)[а-яё-]+\s+){0,3}"
        r"в\s+(?:сизо|следственн\w+\s+изолятор))",
    ),
    (
        EventType.CHARGE,
        # Plural or passive only: «X обвинил военных», «обвинял власти» is X accusing others.
        _W + r"(?:обвинили|обвиня(?:ли|ют|ется|ются)(?![а-яё])|"
        r"обвинен(?:а|ы|о)?(?![а-яё])|предъяв\w*\s+(?:(?:ему|ей|им|их)\s+)?обвинени\w*|"
        r"стал\w*\s+обвиняем\w*)",
        # «обвиняемые» alone is a noun for the people, as «задержанные» is.
    ),
    # «осудил войну» is condemning; a court sentence reads «осудили», «осужден».
    (EventType.SENTENCE, _W + r"(?:приговорил\w*|осудили|осужден(?:а|ы|о)?(?![а-яё]))"),
    (EventType.FINE, _W + r"(?:оштрафова\w*)"),
    (
        EventType.RELEASE,
        # Verb forms only: «иск о его освобождении» talks about a release, it is not one.
        _W + r"(?:освободил\w*|освобожден(?:а|ы|о)?(?![а-яё])|отпустил\w*|"
        r"вышел\w*\s+на\s+свободу)",
    ),
)
_NOUN_TRIGGERS: tuple[tuple[EventType, str], ...] = (
    (EventType.CASE_OPENED, _W + r"(?:уголовн\w*\s+дел\w*)"),
    (EventType.ARREST, _W + r"(?:арест(?:а|е|ом|у)?)(?![а-яё])"),
    (EventType.SENTENCE, _W + r"(?:приговор(?:а|е|ом|у)?)(?![а-яё])"),
    (EventType.FINE, _W + r"(?:штраф\w*)"),
)
# «Его задержали», «Ей предъявили обвинение»: the sentence names nobody, the person is
# the one named before it. Plural pronouns («их», «им», «ими») are never resolved — they
# stand for a group — and «им» is only plural here, since the masculine instrumental
# «ним» is written with the preposition.
_MASCULINE_PRONOUN = re.compile(r"(?<![а-яё])(?:его|ему|нем|нём|него|нему|ним)(?![а-яё])")
_FEMININE_PRONOUN = re.compile(r"(?<![а-яё])(?:ее|её|ей|ней|нее|неё|нею)(?![а-яё])")
# «Его адвоката задержали», «Ее дочь оштрафовали»: the pronoun belongs to the next word,
# and the event is about that person, not about its owner.
_PRONOUN_WITH_OWNER = re.compile(
    r"(?<![а-яё])(?:его|ее|её|их)\s+(?P<owned>[а-яёa-z]+)(?![а-яё])", re.IGNORECASE
)

# «приложило к делу переведенный … приговор», «копию приговора»: the verdict is a
# document here, not an event.
_DOCUMENT_BEFORE = re.compile(
    r"(?<![а-яё])(?:приложил\w*|переведенн\w*|переведённ\w*|текст\w*|копи[юяие]\w*)\s+"
    r"(?:[а-яё-]+\s+){0,3}$"
)
_NEGATION_BEFORE = re.compile(r"(?<![а-яё])не\s+(?:[а-яё]+\s+)?$")
# «отказал в возбуждении дела», «отказался возбуждать», «в возбуждении дела … отказали»:
# a refusal to open a case — there is none. Checked on the whole sentence, so the noun
# trigger «уголовного дела» of the same wording does not count either.
_REFUSED_CASE_OPENING = re.compile(
    r"(?<![а-яё])отказ\w*\s+(?:[а-яё]+\s+){0,2}(?:в\s+)?возбу[дж]|"
    r"(?<![а-яё])в\s+возбу[дж]\w*\s+(?:[а-яё-]+\s+){0,8}отказ"
)
# «до ареста», «после первого ареста», «согласно второму приговору»: a reference to
# another event.
_TEMPORAL_REFERENCE_BEFORE = re.compile(
    # «до ареста», «после первого ареста», «согласно второму приговору», and «иск о его
    # освобождении» — the sentence talks about the event instead of reporting it.
    r"(?<![а-яё])(?:до|после|перед|согласно|об?|с\s+момента|во\s+время)\s+"
    r"(?:его\s+|ее\s+|её\s+|их\s+)?(?:[а-яё]+(?:ого|ему|ому|ой|ым|им)\s+)?$"
)
# The year of a trigger is looked up in its time frame: the part of the sentence between a
# contrast or a present anchor («…, а сегодня его задержали») and the next one. Inside the
# frame, clauses (split at punctuation) that only describe someone — a relative clause
# («…, который в 2012 году победил…») or a participial one («Иванова, осужденного в
# 2024 году, …») — date that description, not the trigger.
#
# A written date is read only where the trigger is: the clause the trigger sits in. A date
# in a clause of its own belongs to whatever that clause says («Дело возбудили из-за доната,
# который он перевел ФБК 5 августа 2021 года» — the 5th of August is the donation).
_MONTHS = {
    "январь": 1,
    "февраль": 2,
    "март": 3,
    "апрель": 4,
    "май": 5,
    "июнь": 6,
    "июль": 7,
    "август": 8,
    "сентябрь": 9,
    "октябрь": 10,
    "ноябрь": 11,
    "декабрь": 12,
}
# A day takes the month in the genitive («12 октября»); sources also write the nominative
# («12 декабрь») and abbreviations («12 окт.»). The genitives are listed rather than
# derived: «мая», «марта» and «июня» are not what the nominatives spell with a suffix.
_MONTH_FORMS = {
    form: number
    for forms, number in (
        (("январь", "января", "янв"), 1),
        (("февраль", "февраля", "февр"), 2),
        (("март", "марта", "мар"), 3),
        (("апрель", "апреля", "апр"), 4),
        (("май", "мая", "ма"), 5),
        (("июнь", "июня", "июн"), 6),
        (("июль", "июля", "июл"), 7),
        (("август", "августа", "авг"), 8),
        (("сентябрь", "сентября", "сен", "сент"), 9),
        (("октябрь", "октября", "окт"), 10),
        (("ноябрь", "ноября", "нояб", "ноя"), 11),
        (("декабрь", "декабря", "дек"), 12),
    )
    for form in forms
}
# Longest first, so «сентября» is not read as «сен» with trailing letters.
_MONTH_ALTERNATION = "|".join(sorted(_MONTH_FORMS, key=len, reverse=True))
_DAY_MONTH = re.compile(
    r"(?<!\d)(?P<day>[12]?\d|3[01])\s+(?P<month>" + _MONTH_ALTERNATION + r")\.?(?![а-яё])"
    r"\s*(?:(?P<year>(?:19|20)\d{2})\s*(?:года|г\.|года)?)?(?!\d)"
)
# «12.10.2025», «12/10/2025». The year is written out here, never guessed. The second
# alternative has no year and needs the guard below: on its own «20.2» is any number.
_NUMERIC_DATE = re.compile(
    r"(?<!\d)(?P<day>[12]?\d|3[01])\.(?P<month>[01]?\d)\.(?P<year>(?:19|20)\d{2})(?!\d)"
    r"|(?<!\d)(?P<day2>[12]?\d)\.(?P<month2>[01]?\d)(?!\.\d)(?!\d)"
)
# A reference to a criminal-code article: «ст. 20.2», «статьи 3.9», «ч. 3 ст. 12.8 КоАП РФ».
# Cut out of the clause before any date is read: «арестовали по ст. 20.2 КоАП РФ» is not an
# arrest on the 20th of February. Only the reference goes — a real date beside it is kept.
# A four-digit tail is not an article, so «20.2.2025» is left for _NUMERIC_DATE to read.
_LEGAL_REFERENCE = re.compile(
    r"(?:ст\.|стать[ьяеию]|ч\.|п\.)\s*\d+\.\d+(?!\.\d)\s*(?:к\s*оап|ук|кодекс)?\s*(?:рф)?"
    r"|\d+\.\d+(?!\.\d)\s*(?:к\s*оап\s*рф|ук\s*рф)"
)
# «12 октября 1990 года рождения»: the date dates the person, not the event. It is read
# only because the bare year alone is not a date, which is no reason to trust the day.
# _DAY_MONTH has already consumed «года» as the year's own ending, so only «рождения»
# and «г. р.» can follow the match.
_BIRTH_AFTER = re.compile(r"\s*(?:рождения|г\.\s*р\.?)\b")
# «под стражу до 15 декабря», «до конца дня 15 декабря», «на срок по 27 ноября»: a date
# behind «до» or «по» ends a measure of restraint, it does not say when the event happened.
# Only a fixed qualifier may sit between them — any «до» in the clause would swallow
# «До суда Петрова арестовали 12 октября», which names a real date. «с» is left out on
# purpose: «с 12 по 15 октября» is a range whose start is the day, and «с 3 октября начался
# процесс» is one.
_UNTIL_DATE = re.compile(
    r"(?:до|по)\s*(?:конца\s+дня|включительно|исключительно|первого|второго)?\s*$"
)
# «12 и 13 октября», «12, 13 октября»: one clause, two days, one month. Only the second
# is a match on its own, so the count of dates would not see the ambiguity.
_ENUMERATED_DAYS = re.compile(
    r"(?<!\d)(?:[12]?\d|3[01])\s*(?:и|или)\s+(?:с\s+)?(?:[12]?\d|3[01])\s+(?:дней\s+)?"
    r"(?:" + _MONTH_ALTERNATION + r")"
)
# A date written without a year may belong to the year before the publication's. It is
# read that way only when it lands shortly before the publication: news is about what has
# just happened, so «12 октября» in an article of 5 May names neither this October (still
# ahead) nor an October seven months back. Such a date is not read at all.
_MAX_PAST_DAYS = 45
_CLAUSE_PUNCTUATION = re.compile(r"[,;:()]|\s[—–-]\s")
_TIME_FRAME_START = re.compile(r"(?<![а-яё])(?:а|но|зато|однако|сегодня|вчера|накануне)(?![а-яё])")
# A subordinate clause («…, что он одобрил поступок Жлобицкого», «…, в котором он назвал
# Жлобицкого»): a person named only there is not the target of the main clause's event.
_SUBORDINATE_CLAUSE_START = re.compile(
    r"^\s*(?:[а-яё]+\s+)?(?:котор[а-яё]*|что|чтобы|где)(?![а-яё])", re.IGNORECASE
)
_CLAUSE_SUBJECT_PRONOUN = re.compile(r"(?<![а-яё])(?:он|она|они)(?![а-яё])", re.IGNORECASE)


# How many person mentions before the sentence must agree on one person for a pronoun to
# be resolved.
_ANTECEDENT_DEPTH = 2


class RuleBasedEventExtractor:
    extractor_name = "rule-based-event-extractor"
    # 1.3.0: charge/sentence verbs in plural or passive only («обвинил военных» is the
    # person accusing); «согласно приговору» is a reference; a sentence naming another
    # year has no event date.
    # 1.3.1: that year is looked up in the trigger's time frame, not the whole sentence.
    # 1.4.0: a pronoun links the event to the single person named before the sentence.
    # 1.5.0: «обвиняемые» is a noun for the people, not a charge.
    # 1.6.0: a verdict that is a document («приложило к делу … приговор») is not an event.
    # 1.7.0: a person named only in a subordinate clause without the trigger, after that
    # clause's own pronoun subject («…, что он одобрил поступок Жлобицкого»), is not the
    # event's target.
    # 1.8.0: the person a crime was aimed at («подготовке убийства … Вадима Волченко») is
    # not the event's target.
    # 1.9.0: «отправили (оказался) в СИЗО» is an arrest; «предъявило ему обвинение» a charge.
    # 1.10.0: «отказал в возбуждении дела», «в возбуждении … отказали» open no case.
    # 1.11.0: `event_date` is only the date the text writes for the event. The publication
    # date is no longer its fallback, so an event whose text names no date has none.
    # 1.12.0: a criminal-code reference is not a date («ст. 20.2 КоАП РФ» is not the 20th
    # of February), a birth date dates the person, a date after «до конца дня» still ends
    # a measure of restraint, and «12 и 13 октября» is one month and two days.
    extractor_version = "1.12.0"

    def __init__(self, morphology: NameMorphology | None = None) -> None:
        self._morphology = morphology or NameMorphology()

    def extract(
        self,
        document: ExtractionDocument,
        mentions: list[NormalizedMention],
    ) -> list[EventMention]:
        events: list[EventMention] = []
        for start, end in _sentence_spans(document.text):
            sentence = document.text[start:end].strip()
            if not sentence:
                continue
            lowered = sentence.lower()
            trigger = self._trigger(lowered)
            if trigger is None:
                continue
            event_type, trigger_text, trigger_start = trigger
            sentence_start = start + document.text[start:end].find(sentence)
            sentence_end = sentence_start + len(sentence)
            links = self._links_for_sentence(
                mentions,
                sentence_start,
                sentence_end,
                document.text,
                trigger_offset=sentence_start + trigger_start,
            )
            if not any(link.role is EventEntityRole.TARGET for link in links):
                antecedent = self._antecedent(mentions, sentence, sentence_start, document.text)
                if antecedent is not None:
                    links.append(
                        EventEntityLink(role=EventEntityRole.TARGET, mention_index=antecedent)
                    )
            events.append(
                EventMention(
                    event_type=event_type,
                    start_offset=sentence_start,
                    end_offset=sentence_end,
                    event_date=_event_date(lowered, trigger_start, document.published_at),
                    confidence=0.72,
                    attributes={"trigger_text": trigger_text},
                    links=links,
                    extractor_name=self.extractor_name,
                    extractor_version=self.extractor_version,
                )
            )
        return sorted(events, key=lambda event: (event.start_offset, event.event_type.value))

    def _trigger(self, lowered_sentence: str) -> tuple[EventType, str, int] | None:
        """The earliest non-negated verb trigger, else the earliest noun trigger, with its start."""
        refused_opening = _REFUSED_CASE_OPENING.search(lowered_sentence) is not None
        for triggers in (_VERB_TRIGGERS, _NOUN_TRIGGERS):
            found: list[tuple[int, EventType, str]] = []
            for event_type, pattern in triggers:
                if event_type is EventType.CASE_OPENED and refused_opening:
                    continue
                for match in re.finditer(pattern, lowered_sentence):
                    prefix = lowered_sentence[: match.start()]
                    if _NEGATION_BEFORE.search(prefix):
                        continue
                    if triggers is _NOUN_TRIGGERS and (
                        _TEMPORAL_REFERENCE_BEFORE.search(prefix) or _DOCUMENT_BEFORE.search(prefix)
                    ):
                        continue
                    found.append((match.start(), event_type, match.group(0)))
                    break
            if found:
                trigger_start, event_type, text = min(found, key=lambda item: item[0])
                return event_type, text, trigger_start
        return None

    def _antecedent(
        self,
        mentions: list[NormalizedMention],
        sentence: str,
        sentence_start: int,
        text: str,
    ) -> int | None:
        """The person a pronoun of this sentence stands for, when only one person can fit.

        Resolved only when the last person named before the sentence is the single
        candidate: any other person mention in between makes the reference ambiguous.
        """
        lowered = sentence.lower()
        masculine = _MASCULINE_PRONOUN.search(lowered) is not None
        feminine = _FEMININE_PRONOUN.search(lowered) is not None
        if masculine == feminine:
            # No pronoun at all, or both genders: nothing to resolve.
            return None
        if self._only_owns_the_next_word(lowered):
            return None
        before = [
            index
            for index, mention in enumerate(mentions)
            if mention.entity_type is EntityType.PERSON and mention.end_offset <= sentence_start
        ]
        if len(before) < _ANTECEDENT_DEPTH:
            return None
        candidate = before[-1]
        # A pronoun does not reach into the previous paragraph.
        if "\n\n" in text[mentions[candidate].end_offset : sentence_start]:
            return None
        # The recent mentions must all be the same person: «Роман Паклин» and «Паклин» are,
        # «Иван Петров» and «Сергей Сидоров» are not.
        surname = self._surname_keys(mentions[candidate].normalized_text)
        if not surname or any(
            not (surname & self._surname_keys(mentions[index].normalized_text))
            for index in before[-_ANTECEDENT_DEPTH:]
        ):
            return None
        name = mentions[candidate].normalized_text
        gender = self._morphology.gender_of(name)
        if gender is None or gender != ("masc" if masculine else "femn"):
            return None
        return candidate

    def _surname_keys(self, name: str) -> frozenset[str]:
        words = name.split()
        return self._morphology.name_normal_forms(words[-1]) if words else frozenset()

    def _only_owns_the_next_word(self, lowered_sentence: str) -> bool:
        """Every pronoun of the sentence is possessive («его адвоката», «ее дочь»)."""
        owners = list(_PRONOUN_WITH_OWNER.finditer(lowered_sentence))
        if not owners:
            return False
        pronouns = len(_MASCULINE_PRONOUN.findall(lowered_sentence)) + len(
            _FEMININE_PRONOUN.findall(lowered_sentence)
        )
        possessive = sum(
            1 for match in owners if not self._morphology.is_verb(match.group("owned"))
        )
        return possessive >= pronouns

    @staticmethod
    def _links_for_sentence(
        mentions: list[NormalizedMention],
        start: int,
        end: int,
        text: str = "",
        *,
        trigger_offset: int | None = None,
    ) -> list[EventEntityLink]:
        links: list[EventEntityLink] = []
        for index, mention in enumerate(mentions):
            if mention.start_offset < start or mention.end_offset > end:
                continue
            if mention.entity_type is EntityType.PERSON and (
                _is_non_subject(text, mention, start, end)
                or _in_a_clause_without_the_trigger(text, mention, start, end, trigger_offset)
            ):
                continue
            role = _role_for_entity_type(mention.entity_type)
            links.append(EventEntityLink(role=role, mention_index=index))
        return links


def _event_date(
    lowered_sentence: str, trigger_start: int, published_at: datetime | None
) -> datetime | None:
    """The date the text writes for this event, or None when it writes none.

    `published_at` is never a fallback: it is the article's own date, and putting it here
    made every dated event look like it happened on the day its article came out. It is
    used for one thing only — to fill in the year of a date written without one, and only
    where that year is unambiguous.
    """
    frame_start, frame_end = 0, len(lowered_sentence)
    for match in _TIME_FRAME_START.finditer(lowered_sentence):
        if match.start() <= trigger_start:
            frame_start = match.start()
        else:
            frame_end = match.start()
            break
    frame = lowered_sentence[frame_start:frame_end]
    trigger_in_frame = trigger_start - frame_start
    cuts = sorted(
        {0, len(frame)}
        | {index for match in _CLAUSE_PUNCTUATION.finditer(frame) for index in match.span()}
    )
    # A date written in another clause of the frame dates that clause, not this trigger.
    clause = next(
        (frame[start:end] for start, end in pairwise(cuts) if start <= trigger_in_frame < end),
        frame,
    )
    written = _written_dates(clause)
    if len(written) != 1:
        # No date, or two of them: an ambiguous date is no date.
        return None
    day, month, year = written[0]
    if year is not None:
        return _as_datetime(day, month, year)
    return _year_from_publication(day, month, published_at)


def _written_dates(clause: str) -> list[tuple[int, int, int | None]]:
    """(day, month, year as written) of every date the clause spells out, in order."""
    # Criminal-code references are cut out first, so «ст. 20.2» is never read as a date and
    # a real date written beside it still is.
    clause = _LEGAL_REFERENCE.sub(" ", clause)
    # «12 и 13 октября» is one month and two days: which of them the event is, is undecidable.
    if _ENUMERATED_DAYS.search(clause):
        return []
    found: list[tuple[int, int, int | None]] = []
    for match in _DAY_MONTH.finditer(clause):
        month = _month_of(match.group("month"))
        if month is None or _UNTIL_DATE.search(clause[: match.start()]):
            continue
        if _BIRTH_AFTER.match(clause[match.end() :]):
            continue
        found.append((int(match.group("day")), month, _year_of(match.group("year"))))
    for match in _NUMERIC_DATE.finditer(clause):
        month = int(match.group("month") or match.group("month2"))
        day = int(match.group("day") or match.group("day2"))
        year = int(match.group("year")) if match.group("year") else None
        if month <= 12 and not _UNTIL_DATE.search(clause[: match.start()]):
            found.append((day, month, year))
    return found


def _month_of(form: str) -> int | None:
    return _MONTH_FORMS.get(form)


def _year_of(written: str | None) -> int | None:
    return int(written) if written else None


def _as_datetime(day: int, month: int, year: int) -> datetime | None:
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        # «31 февраля» is written but does not exist.
        return None


def _year_from_publication(day: int, month: int, published_at: datetime | None) -> datetime | None:
    """A date written without a year, dated by the article's own year.

    The year is unambiguous only where the date it names has already passed by publication:
    an article of 19 October saying «12 октября» means this year, one of 3 January saying
    «28 декабря» means the year before. A date still ahead of the publication, and one that
    would only fit by landing far in the past, are not read at all — either it belongs to
    another sentence or no year can be claimed for it.
    """
    if published_at is None:
        return None
    published_day = published_at.astimezone(UTC).date()
    this_year = _as_datetime(day, month, published_at.year)
    if this_year is not None and 0 <= (published_day - this_year.date()).days:
        return this_year
    last_year = _as_datetime(day, month, published_at.year - 1)
    if last_year is None:
        return None
    days_back = (published_day - last_year.date()).days
    return last_year if 0 <= days_back <= _MAX_PAST_DAYS else None


def _is_non_subject(text: str, mention: NormalizedMention, start: int, end: int) -> bool:
    """A reporting source, a procedural actor or a victim in the event sentence: not its
    subject.

    Ambiguous cases stay unlinked: a wrongly attributed event is worse than a missed one.
    """
    if not text:
        return False
    before = text[max(start, mention.start_offset - _CONTEXT_CHARS) : mention.start_offset]
    after = text[mention.end_offset : min(end, mention.end_offset + _CONTEXT_CHARS)]
    victim_before = text[
        max(start, mention.start_offset - _VICTIM_CONTEXT_CHARS) : mention.start_offset
    ]
    return bool(
        _VICTIM_BEFORE_NAME.search(victim_before)
        or _ROLE_AS_FIRST_TOKEN.match(mention.surface_text)
        or _ROLE_BEFORE_NAME.search(before)
        or _SOURCE_BEFORE_NAME.search(before)
        or _SPEECH_AFTER_NAME.match(after)
    )


def _in_a_clause_without_the_trigger(
    text: str, mention: NormalizedMention, start: int, end: int, trigger_offset: int | None
) -> bool:
    """The person is named only in a subordinate clause that does not hold the trigger,
    after a pronoun that is that clause's own subject («…, что он одобрил поступок
    Жлобицкого»). A person who is the clause's subject («приговор, которым Люлюков
    признан виновным») is still the one the event is about."""
    if not text or trigger_offset is None:
        return False
    sentence = text[start:end]
    cuts = sorted(
        {0, len(sentence)}
        | {index for match in _CLAUSE_PUNCTUATION.finditer(sentence) for index in match.span()}
    )
    position = mention.start_offset - start
    for clause_start, clause_end in pairwise(cuts):
        if clause_start <= position < clause_end:
            clause = sentence[clause_start:clause_end]
            if not _SUBORDINATE_CLAUSE_START.match(clause):
                return False
            if _CLAUSE_SUBJECT_PRONOUN.search(clause[: position - clause_start]) is None:
                return False
            return not clause_start <= trigger_offset - start < clause_end
    return False


def _role_for_entity_type(entity_type: EntityType) -> EventEntityRole:
    if entity_type is EntityType.PERSON:
        return EventEntityRole.TARGET
    if entity_type is EntityType.COURT:
        return EventEntityRole.COURT
    if entity_type is EntityType.ORGANIZATION:
        return EventEntityRole.AUTHORITY
    if entity_type is EntityType.LEGAL_REFERENCE:
        return EventEntityRole.LEGAL_BASIS
    return EventEntityRole.LOCATION


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char == "\n":
            if start < index:
                spans.append((start, index))
            start = index + 1
            continue
        if char not in ".!?":
            continue
        if (
            char == "."
            and index > 0
            and text[index - 1].isupper()
            and (index == 1 or text[index - 2].isspace())
        ):
            continue
        prefix = text[max(start, index - 4) : index + 1].lower()
        if any(prefix.endswith(abbreviation) for abbreviation in _ABBREVIATIONS):
            continue
        if index + 1 < len(text) and not text[index + 1].isspace():
            continue
        spans.append((start, index + 1))
        start = index + 1
    if start < len(text):
        spans.append((start, len(text)))
    return spans

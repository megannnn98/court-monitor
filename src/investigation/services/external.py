"""Retrievers of sources outside the database: one configured source search each.

What is found outside stays the evidence of this investigation: nothing is stored,
extracted or grouped (ADR 0027). A post outside says a text has these words; it is
never a fact and never decides who the subject is.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from functools import lru_cache

from pymorphy3 import MorphAnalyzer

from ingestion.search import SourceSearch, SourceSearchQuery
from investigation.models import EvidenceItem, InvestigationQuery, RetrieverCapabilities
from investigation.services.charges import CHARGE, russian_charge
from investigation.services.retrieval import TEXT_FACTS

EXTERNAL_RESULTS = 10
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
# Letters a declined word of a name may differ in at its end.
_ENDING = 3
_MIN_STEM = 3
# How far apart the words of a name may stand in a text and still be the name: a name is
# written together, and a text that writes it in two places writes two people. Three
# tokens, not four: at four «Андрей Карлов заявил, что Игорь Сечин не приходил» is a
# text about Игоря Карлова. On the hand-annotated set a wider window cuts nothing extra,
# so the tighter one costs nothing and refuses that text.
_NAME_WINDOW = 3


def _tokens(text: str) -> list[tuple[str, bool, bool]]:
    """The words of a text as written: folded, whether it is an initial («И.»), and
    whether it is written with a capital."""
    result: list[tuple[str, bool, bool]] = []
    for match in _WORD.finditer(text):
        word = match.group()
        folded = word.lower().replace("ё", "е")
        capital = word[:1].isupper()
        initial = len(folded) == 1 and capital and text[match.end() :].startswith(".")
        result.append((folded, initial, capital))
    return result


# A surname the dictionary has never heard of is declined by the wrong rule: «Дудя» comes
# back as the inflected form of the verb «дудеть», and nothing of the name is left to
# compare. There the words are one only if they differ by an ending and nothing else: the
# shorter must be all but one or two of its letters at the start of the longer, and the
# longer no more than two letters longer. That is what tells «Дудя» of «Дудь» from «Игорь
# Карпов» of «Игорь Карлов», which differ by a letter and a half and are two people.
_ENDING_GIVE_UP = 2
_ENDING_GROWTH = 2

# A name looked for as one word may be a place, and a place is declined by an adjective:
# «Тюменской» of «Тюмень». That is a longer ending than a name's, so it has its own
# allowance — and it is reached only where the dictionary knows neither word as a person's
# name, so it never makes «Петров» and «Петровский» one word.
_PLACE_ROOT = 4
_PLACE_GROWTH = 5

# The tags under which the dictionary means a person rather than a common word.
_PERSON_TAGS = frozenset({"Name", "Surn", "Patr", "Abru"})


@lru_cache(maxsize=32768)
def _named(word: str) -> bool:
    """Whether the dictionary knows this word as a person's name or surname. The tag is
    read as written — `NOUN,anim,masc,Name sing,nomn` — and its grammemes looked for."""
    return any(
        grammeme in _PERSON_TAGS
        for parsed in _morph().parse(word)
        for grammeme in str(parsed.tag).replace(",", " ").split()
    )


def _ends_alike(one: str, other: str) -> bool:
    """Whether two words are one word written in two cases: all but an ending in common."""
    shorter, longer = sorted((one, other), key=len)
    return longer.startswith(shorter[:-_ENDING_GIVE_UP]) and (
        len(longer) - len(shorter) <= _ENDING_GROWTH
    )


@lru_cache(maxsize=32768)
def _forms(word: str) -> frozenset[str]:
    """Every form the dictionary finds in a word: `халилова` is `халилов` and
    `халиловый` alike, and the first of the two is the one that means the person."""
    return frozenset(parsed.normal_form for parsed in _morph().parse(word)) or frozenset({word})


@lru_cache(maxsize=32768)
def _surname_forms(word: str) -> frozenset[str]:
    """Dictionary forms of every reading which is explicitly a surname.

    A rare surname can have an unrelated first reading: pymorphy reads «Кынева» as a
    place or a female given name before its masculine surname reading. Conversely, all
    readings must not be used here: «Андреев» also has an inflected-name reading of
    «Андрей», but its surname reading is a different word.
    """
    return frozenset(parsed.normal_form for parsed in _morph().parse(word) if "Surn" in parsed.tag)


def _matches(token: str, *, initial: bool, wanted: str, place: bool = False) -> bool:
    """Whether one word of a text is the word of a name looked for, in any case of the
    noun, or an initial standing for it.

    Two words of a name are the same word when the dictionary says so — the same written
    form, or the same dictionary form. A shared root is not enough: «Карлов» and «Карпов»
    share three letters and are two surnames, and so are «Смирнов» under «Алексей» and
    under «Александр». Where the dictionary knows neither word as a person's name there
    may be an ending between them, and then it must be only an ending.
    """
    if initial:
        return wanted.startswith(token)
    if len(token) < 2 or len(wanted) < 2:
        return token == wanted
    if token == wanted:
        return True
    if _surname_forms(token) & _surname_forms(wanted):
        return True
    # The dictionary's forms of a word it declined are that word's own. Only a word that
    # stands declined may be looked for by them: otherwise «Владимир» answers to
    # «Владимиров», and the two are a given name and a patronymic.
    forms = _forms(token)
    if _lemma(token) != token and wanted in forms:
        return True
    wanted_forms = _forms(wanted)
    if _lemma(wanted) != wanted and token in wanted_forms:
        return True
    if _named(token) or _named(wanted):
        return False
    if place:
        shorter, longer = sorted((token, wanted), key=len)
        if (
            len(shorter) >= _PLACE_ROOT
            and longer.startswith(shorter[:_PLACE_ROOT])
            and (len(longer) - len(shorter) <= _PLACE_GROWTH)
        ):
            return True
    # A place's allowance is added to an ending's, not put in its place: a one-word name
    # may be a surname too, and «Дудя» is «Дудь» whichever of the two was looked for.
    return _ends_alike(token, wanted)


def names(text: str, name: str) -> bool:
    """The name stands in the text, in any case of the noun and in any order, and its
    words stand together.

    Standing anywhere was the noise: with the first three letters of each word matched
    (`max(3, len - 3)`), a text about «Андрей Карлов» and «Игорь Сечин» was about
    «Игорь Карлов» — it holds both names in one text and they have nothing to do with
    each other. Now every word must be found within a few words of the others, in any
    order and any case, and an initial counts for the word it stands for.

    A name of one word — a surname, or the place an unnamed publication is looked up by —
    is matched as one word, with no proximity to demand: there is nothing to be near.
    No name, no filter.
    """
    wanted = [word.lower().replace("ё", "е") for word in _WORD.findall(name) if len(word) > 1]
    if not wanted:
        return True
    tokens = _tokens(text)
    if not tokens:
        return False
    if len(wanted) == 1:
        return any(
            _matches(token, initial=initial, wanted=wanted[0], place=True)
            for token, initial, _capital in tokens
        )
    for start in range(len(tokens)):
        window = tokens[start : start + _NAME_WINDOW]
        if all(
            any(
                _matches(token, initial=initial, wanted=word) for token, initial, _capital in window
            )
            for word in wanted
        ):
            return True
    return False


# A word looked for stands in a text in another case of the noun («статья» in «по
# статье», «статьёй»), and where the dictionary says the two are one root («обвинение»
# of «обвинён», «арест» of «арестован»). A prefix alone is not enough: «статус» is not
# «статья». The dictionary is pymorphy3, deterministic and offline, as everywhere else
# in the pipeline; a word it does not know is left as written.
_TERM_PREFIX = 5
_LETTERS_ONLY = re.compile(r"[^\W\d_]+(?:\s+[^\W\d_]+)*", re.UNICODE)
# A charge has to be part of a claim about this person, rather than a legal reference
# which happened to be in the same article. The words before the citation matter: they
# cover both «Марию обвинили …» and «Задержана Мария. Ей вменяют …», while refusing an
# article about Ivan followed by an interview with Maria.
#
# One list of accusation words, not two. «Дело возбуждено по …» and «по статье …» on their
# own are a legal reference, not an assertion about a person, so they are not in it; a
# second, wider list would have made the first one dead, since every word of the narrow
# list is also in the wide one.
_CHARGE_ASSERTION = re.compile(
    r"\b(?:"
    r"обвин\w*|вмен\w*|подозрев\w*|осужд\w*|приговор\w*|фигурант\w*"
    r"|возбуд\w*(?:\s+[^\W\d_]+){0,3}\s+дел\w*"
    r")",
    re.IGNORECASE,
)
# How many words before a citation may carry the accusation and the name. Wide enough for
# a sentence («Суд назначил задержание Марии Бонцлер, которой вменяют …»), narrow enough
# that a person named in one paragraph does not claim the article of another.
_CHARGE_NAME_WORDS = 25


@lru_cache(maxsize=1)
def _morph() -> MorphAnalyzer:
    return MorphAnalyzer()


@lru_cache(maxsize=32768)
def _lemma(word: str) -> str:
    """The word as the dictionary writes it: one form for every case of the noun."""
    parsed = _morph().parse(word)
    return parsed[0].normal_form if parsed else word


def _same_word(one: str, other: str) -> bool:
    """Two words are one word: the dictionary's own forms of it, or one root of at
    least five letters («обвин-» of «обвинение» and «обвиняется», «стат-» is too short
    and is left to «статья» alone)."""
    if one == other:
        return True
    shared = 0
    for left, right in zip(one, other, strict=False):
        if left != right:
            break
        shared += 1
    return shared >= _TERM_PREFIX


def tells(text: str, terms: tuple[str, ...]) -> bool:
    """One of the words looked for stands in the text, in any case of the noun; a term
    that is not only words («205.2») is compared as written. A text that only names the
    person tells nothing of the fact it was looked up for."""
    folded = text.lower().replace("ё", "е")
    held = {_lemma(word) for word in _WORD.findall(folded) if len(word) > 1}
    if not held:
        return False
    for term in terms:
        wanted = " ".join(term.lower().replace("ё", "е").split())
        if not wanted:
            continue
        if not _LETTERS_ONLY.fullmatch(wanted):
            if re.search(r"(?<![\w])" + re.escape(wanted), folded):
                return True
            continue
        if all(any(_same_word(_lemma(word), seen) for seen in held) for word in wanted.split()):
            return True
    return False


def charge_about(name: str, texts: Sequence[str]) -> set[str]:
    """Every charge of the Russian Criminal Code these texts lay at ``name``'s door."""
    return set().union(*(charges_of(text, name) for text in texts)) if texts else set()


def charges_of(text: str, name: str) -> set[str]:
    """Every charge of the Russian Criminal Code this text makes an accusation about
    ``name``, in the one spelling `investigation.services.charges` writes.

    A citation alone says nothing: «ст. 360 УК» in a story about somebody else, or a law
    quoted in passing, is not a charge of this person. The words before it are what decide
    — the name and the accusation standing in one local window — and the law has to be the
    Russian one.

    Deliberately not sentence-based: abbreviations such as ``ч.`` and ``ст.`` and initials
    carry dots. For every citation the words before it are read — but only back to the
    citation before it, never across one. That is where one person's case ends and
    another's begins in a text that speaks of two: «Марию Бонцлер обвинили по ст. 207.3
    УК РФ. А Иванову вменяют ст. 280 УК РФ» has two cases, and the second article is not
    the first person's.

    The enumeration «по ч. 1 ст. 280 УК РФ и ч. 2 ст. 282 УК РФ» is one case written in
    two citations, and nothing but a conjunction stands between them — so the second takes
    the verdict of the first. A single letter is not a word: «ч.» is a part, not a name.
    """
    words = list(_WORD.finditer(text))
    if not words:
        return set()
    found: set[str] = set()
    previous = 0
    carried: str | None = None
    for match in CHARGE.finditer(text):
        spelling = russian_charge(match, text)
        if spelling is not None:
            article_word = next(
                (index for index, word in enumerate(words) if word.end() > match.start()),
                len(words),
            )
            back = max(0, article_word - _CHARGE_NAME_WORDS)
            floor = next((index for index, word in enumerate(words) if word.start() >= previous), 0)
            start = words[max(back, floor)].start() if words else 0
            context = text[start : match.start()]
            if not names(context, name) and _only_connectives(context) and carried is not None:
                # One case, written in two citations: the name and the accusation of the
                # first are the name and the accusation of the second.
                context = carried
            elif names(context, name):
                carried = context
            if names(context, name) and _CHARGE_ASSERTION.search(context):
                found.add(spelling)
        previous = match.end()
    return found


# What may stand between two citations of one case and still be the same case. Everything
# else — a name, a verb, a place — starts somebody else's.
_CONNECTIVE = frozenset(
    {"и", "а", "также", "по", "кроме", "того", "либо", "или", "а также", "а_также", "ли", "да"}
)


def _only_connectives(between: str) -> bool:
    words = [word.lower().replace("ё", "е") for word in _WORD.findall(between)]
    return bool(words) and all(word in _CONNECTIVE or len(word) == 1 for word in words)


def _charge_lead(text: str, name: str) -> bool:
    """Whether a Criminal Code article is an accusation about ``name`` at all."""
    return bool(charges_of(text, name))


class SourceSearchRetriever:
    """A configured source's own search, asked by words: the name and the first of the
    words looked for, as a person would type them into its search box."""

    def __init__(self, search: SourceSearch, *, source: str, limit: int = EXTERNAL_RESULTS) -> None:
        self._search = search
        self._limit = limit
        self.name = f"{source}:{search.source}"
        self.capabilities = RetrieverCapabilities(
            source=source,
            local=False,
            network=True,
            kinds=frozenset({"keywords"}),
            facts=TEXT_FACTS,
            source_types=frozenset({"external"}),
        )

    async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
        if query.kind != "keywords":
            return []
        # A site's search box reads every word as required: the name and one word.
        words = " ".join(part for part in (query.name, *query.terms[:1]) if part)
        found = await self._search.search(
            SourceSearchQuery(
                text=words,
                limit=self._limit,
                published_from=query.published_from,
                published_to=query.published_to,
            )
        )
        # A site's search matches loosely; a post that does not name the person (every
        # word of the name, in any case) is not about them.
        found = [item for item in found if names(item.text, query.name)]
        now = datetime.now(UTC)
        return [
            EvidenceItem(
                source_type="external",
                source_id=self.name,
                source_name=item.source_name,
                external_id=item.external_id,
                url=item.url,
                title=item.title,
                text=item.text,
                published_at=item.published_at,
                retrieved_at=now,
                score_type="source_order",
                leads=self._leads(query, item.text),
            )
            for item in found
        ]

    @staticmethod
    def _leads(query: InvestigationQuery, text: str) -> tuple[str, ...]:
        """The fact a text was looked up for is its lead only if the text holds one of the
        words looked for. A site's search matches loosely, and only the first of the
        words is sent: a text that merely names the person would otherwise count as a
        lead, and a scenario stops looking for a fact it has a lead for."""
        if query.fact is None or (query.terms and not tells(text, query.terms)):
            return ()
        if query.fact == "charges" and not _charge_lead(text, query.name):
            # A bare «ст. 360 УК» in a story about somebody else must not close a
            # follow-up.  Extraction may still refuse the candidate afterwards.
            return ()
        return (query.fact,)

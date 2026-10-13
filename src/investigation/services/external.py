"""Retrievers of sources outside the database: one configured source search each.

What is found outside stays the evidence of this investigation: nothing is stored,
extracted or grouped (ADR 0027). A post outside says a text has these words; it is
never a fact and never decides who the subject is.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from functools import lru_cache

from pymorphy3 import MorphAnalyzer

from ingestion.search import SourceSearch, SourceSearchQuery
from investigation.models import EvidenceItem, InvestigationQuery, RetrieverCapabilities
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
        return (query.fact,)

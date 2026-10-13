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


def names(text: str, name: str) -> bool:
    """Every word of the name stands in the text, in any case of the noun; no name, no
    filter."""
    words = [word.lower().replace("ё", "е") for word in _WORD.findall(name) if len(word) > 1]
    if not words:
        return True
    folded = text.lower().replace("ё", "е")
    return all(
        re.search(
            r"(?<![а-яa-z])" + re.escape(word[: max(_MIN_STEM, len(word) - _ENDING)]),
            folded,
        )
        for word in words
    )


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

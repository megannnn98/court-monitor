"""Which court tries a case of this article from this place — as the operator's base
has seen it.

To find the sentence of a person a news item does not name, the operator works out the
court: «статья 205.1, Алтайский край — 2-й Восточный окружной военный суд», and looks
for the case on that court's site. The base already holds thousands of such answers: each
record with a sentence says the region, the articles and the court. So the courts are
counted, not derived from the procedure code: for a place and an article, the courts that
sentenced the people of the base from there under it, the most frequent first.

A count is what happened, not a rule: an article tried by district courts gives many
courts with a few cases each, and the answer says so by showing the numbers.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from entities.base_candidates import article_numbers
from entities.unnamed import place_stems

SHOWN = 3

_SITE = re.compile(r"^(https?://[^/\s]+)")


@dataclass(frozen=True)
class CourtHint:
    court: str
    # How many people of the base from the place this court sentenced under the article.
    cases: int
    # The court's site, as the base's links to its case cards give it; None for a court
    # the base has no link to.
    site: str | None


@dataclass(frozen=True)
class CourtHints:
    shown: list[CourtHint]
    # People of the base from the place sentenced under the article, by any court.
    total: int


@dataclass(frozen=True)
class _Sentence:
    place: str
    articles: frozenset[str]
    court: str


class Jurisdiction:
    """The sentences of the base, to ask which courts passed them."""

    def __init__(self, rows: Iterable[tuple[str, str, str, str, str | None]]) -> None:
        """Rows of region, city, articles, court, and the link to the case card."""
        self._sentences: list[_Sentence] = []
        sites: dict[str, Counter[str]] = defaultdict(Counter)
        for region, city, articles, court, card_url in rows:
            if court:
                self._sentences.append(
                    _Sentence(
                        f"{region} {city}".lower(), frozenset(article_numbers(articles)), court
                    )
                )
            site = _SITE.match(card_url or "")
            if site and court:
                sites[court][site[1]] += 1
        self._sites = {court: found.most_common(1)[0][0] for court, found in sites.items()}

    @classmethod
    def from_session(cls, session: Session) -> Jurisdiction:
        return cls(
            tuple(row)
            for row in session.execute(
                text(
                    """
                    SELECT coalesce(region, ''), coalesce(city, ''), coalesce(articles, ''),
                           court, court_card_url
                    FROM airtable_known_persons
                    WHERE court IS NOT NULL
                    """
                )
            )
        )

    def courts(self, place: str, articles: Iterable[str], *, shown: int = SHOWN) -> CourtHints:
        """The courts that sentenced people from `place` under any of `articles`.

        Nothing for a place or an article the text does not give: every court of the
        country is no answer."""
        stems = place_stems(place)
        wanted = {str(article) for article in articles}
        found: Counter[str] = Counter(
            sentence.court
            for sentence in self._sentences
            if wanted & sentence.articles and any(stem in sentence.place for stem in stems)
        )
        ranked = sorted(found.items(), key=lambda item: (-item[1], item[0]))
        return CourtHints(
            [CourtHint(court, cases, self._sites.get(court)) for court, cases in ranked[:shown]],
            sum(found.values()),
        )

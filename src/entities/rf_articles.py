"""The articles of the Criminal Code a person is put on the Rosfinmonitoring list for.

The list is the operator's own, told on 29.09.2026: «все 205-ые, все 281, 208, все 282;
иногда 280, 207.3». A person charged under one of the first is to be on the перечень; one
who is not there yet is the one worth looking at — a fresh case whose entry has not
appeared, or a name the news writes otherwise than the list.

It says what to expect, never who is listed: only an entry of a snapshot does that. And
it is silent about a person whose article the news did not name — most of them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from entities.rf_check import FULL

# «Все 205-ые»: the article and every numbered one under it — 205, 205.1 … 205.6.
ALWAYS_FAMILIES = ("205", "281", "282")
ALWAYS = frozenset({"208"})
# Put on the list for these now and then: nothing to wait for.
SOMETIMES = frozenset({"280", "207.3"})

LISTED = "listed"
AWAITED = "awaited"
SOMETIMES_KIND = "sometimes"


def _always(article: str) -> bool:
    return article in ALWAYS or article.split(".")[0] in ALWAYS_FAMILIES


@dataclass(frozen=True)
class Listing:
    """What a person's articles say of the list, beside what the list itself says."""

    kind: str
    articles: tuple[str, ...]

    @property
    def text(self) -> str:
        named = ", ".join(self.articles)
        if self.kind == LISTED:
            return f"статья перечня: {named} — в перечне"
        if self.kind == AWAITED:
            return f"статья перечня: {named} — в перечне пока нет"
        return f"статья {named} — в перечень включают иногда"


def listing(articles: Iterable[str], rf_level: str | None) -> Listing | None:
    """None for a person none of whose articles leads to the list.

    «В перечне» is said only of a match with the patronymic: a name and a surname alone
    may be a namesake's entry, and the person is then still awaited."""
    known = list(dict.fromkeys(articles))
    always = tuple(article for article in known if _always(article))
    if always:
        return Listing(LISTED if rf_level == FULL else AWAITED, always)
    sometimes = tuple(article for article in known if article in SOMETIMES)
    return Listing(SOMETIMES_KIND, sometimes) if sometimes else None

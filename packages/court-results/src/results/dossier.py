"""What the database holds of one person: the entity group, the verdicts on it and the
publications behind them. Read only; every row is what the pipeline wrote.

A person is an entity group (ADR 0024), named by its key. `persons` is not read.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from analysis import read as verdicts
from analysis.service import GroupFollowers
from ingestion import read as articles_api
from processing import read as identity
from processing.evidence.publications import latest_first, person_evidence, quoted
from processing.read import NAME_SEPARATORS

# Characters of an article's text kept on each side of a mention or of a found word.
QUOTE_CONTEXT = 160
# A word of a name is matched by its beginning: «Иванова» finds «Иванов».
_ENDING = 2
_MIN_STEM = 3
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True)
class EntityHit:
    key: str
    name: str
    mention_count: int
    article_count: int
    last_published_at: datetime | None
    # Every word asked for is a whole word of the name, not only its beginning.
    exact: bool


@dataclass(frozen=True)
class StatedFact:
    """One thing the pipeline decided of a person, with the words it rests on."""

    fact: str
    value: str
    # The quote or the reason the step kept with its answer; may be empty.
    basis: str
    published_at: datetime | None = None


@dataclass(frozen=True)
class PublicationQuote:
    article_id: int
    title: str
    url: str | None
    source: str
    published_at: datetime | None
    quote: str
    # Offsets of the quote in the article's text.
    span_start: int
    span_end: int


def _fold(value: str) -> str:
    return value.lower().replace("ё", "е")


# What parts a written name into words, besides a space: the set
# `processing.read.groups_named_like` parts names by in SQL, so both mean the same by
# «a word of the name».
_TO_SPACES = str.maketrans(NAME_SEPARATORS, " " * len(NAME_SEPARATORS))


def name_words(name: str) -> list[str]:
    return [_fold(word) for word in _WORD.findall(name) if len(word) >= 2]


def _own_words(name: str) -> set[str]:
    """The words of a stored name, parted as `_spaced` parts them in SQL."""
    return set(_fold(name).translate(_TO_SPACES).split(" "))


# A surname declined as an adjective loses three letters: «Поднебесного» is
# «Поднебесный», whose beginning is «поднебесн», not «поднебесно».
_ADJECTIVE_ENDINGS = ("ого", "его", "ому", "ему")


def _stem(word: str) -> str:
    ending = 3 if word.endswith(_ADJECTIVE_ENDINGS) else _ENDING
    return word[: max(_MIN_STEM, len(word) - ending)]


def find_entities(session: Session, name: str, *, limit: int) -> list[EntityHit]:
    """The entity groups whose name holds every word asked for, the most mentioned
    first. A word is matched by its beginning, so a declined name finds its person; the
    beginning must begin a word of the name, so «иван» does not find «Ливанов». The
    whole filter runs in SQL, under its LIMIT: no row is fetched to be dropped."""
    words = name_words(name)
    if not words:
        return []
    return [
        EntityHit(
            key=group.key,
            name=group.name,
            mention_count=group.mention_count,
            article_count=group.article_count,
            last_published_at=group.last_published_at,
            exact=set(words) <= _own_words(group.name),
        )
        for group in identity.groups_named_like(
            session, [_stem(word) for word in words], limit=limit
        )
    ]


def entity_facts(session: Session, key: str) -> list[StatedFact] | None:
    """What the steps decided of the person; None when no group has this key."""
    group = identity.groups_by_key(session, [key]).get(key)
    if group is None:
        return None
    facts: list[StatedFact] = []
    role = identity.roles(session, [group.id]).get(group.id)
    if role is not None:
        value = role.role if role.kind is None else f"{role.role} ({role.kind})"
        facts.append(StatedFact("role", value, role.quote or role.reason))
    charges = sorted(
        {charge.article for charge in identity.charges(session, [group.id]).get(group.id, [])}
    )
    if charges:
        facts.append(StatedFact("charges", ", ".join(charges), ""))
    politics = verdicts.politics(session, [group.id]).get(group.id)
    if politics is not None:
        facts.append(StatedFact("politics", politics.verdict, politics.quote or politics.reason))
    levels = sorted(verdicts.list_levels(session, [group.id]).get(group.id, set()))
    if levels:
        facts.append(StatedFact("rf_list", ", ".join(levels), ""))
    news = verdicts.case_news(session, [group.id]).get(group.id)
    if news is not None:
        facts.append(
            StatedFact("latest_news", news.kind, news.quote or news.reason, news.published_at)
        )
    return facts


def entity_evidence(session: Session, key: str, *, limit: int) -> list[PublicationQuote]:
    """The places the publications name the person, the latest first."""
    group = identity.groups_by_key(session, [key]).get(key)
    if group is None:
        return []
    evidence = person_evidence(
        session, GroupFollowers(), groups=[group.id], context=QUOTE_CONTEXT, quotes=False
    )
    latest = sorted(
        evidence,
        key=lambda item: (*latest_first(item.published_at), -item.article_id, item.quote_start),
    )[:limit]
    return [
        PublicationQuote(
            article_id=item.article_id,
            title=item.title or "",
            url=item.url,
            source=item.source,
            published_at=item.published_at,
            quote=item.quote or "",
            span_start=item.quote_start,
            span_end=item.quote_start + len(item.quote or ""),
        )
        for item in quoted(session, latest, QUOTE_CONTEXT)
    ]


def _quotes_of(
    rows: Sequence[articles_api.StoredArticle], stems: Sequence[str], *, limit: int
) -> list[PublicationQuote]:
    """One quote per article: the text around the first of `stems` found in it."""
    found: list[PublicationQuote] = []
    for row in rows:
        body = row.text or ""
        folded = _fold(body)
        positions = [position for stem in stems if (position := folded.find(stem)) >= 0]
        if not positions:
            continue
        start = max(min(positions) - QUOTE_CONTEXT, 0)
        end = min(min(positions) + QUOTE_CONTEXT, len(body))
        found.append(
            PublicationQuote(
                article_id=row.id,
                title=row.title or "",
                url=row.url,
                source=row.source_name,
                published_at=row.published_at,
                quote=body[start:end],
                span_start=start,
                span_end=end,
            )
        )
        if len(found) >= limit:
            break
    return found


def entity_publication_quotes(
    session: Session, key: str, terms: Sequence[str], *, limit: int, articles: int = 50
) -> list[PublicationQuote]:
    """Where the person's own publications say one of `terms` (a word or its
    beginning): one quote per article, the latest first."""
    stems = [_fold(term) for term in terms if term.strip()]
    if not stems:
        return []
    group = identity.groups_by_key(session, [key]).get(key)
    if group is None:
        return []
    own = {
        (item.article_id, item.published_at)
        for item in person_evidence(session, GroupFollowers(), groups=[group.id], quotes=False)
    }
    latest = [
        article_id
        for article_id, _ in sorted(own, key=lambda pair: (*latest_first(pair[1]), -pair[0]))
    ][:articles]
    texts = articles_api.stored_articles(session, latest)
    return _quotes_of(
        [texts[article_id] for article_id in latest if article_id in texts], stems, limit=limit
    )


def stored_publication_quotes(session: Session, name: str, *, limit: int) -> list[PublicationQuote]:
    """The stored publications whose text holds every word of the name, whether or not
    an entity group was made of them: one quote per article, around the surname."""
    words = name_words(name)
    if not words:
        return []
    stems = [_stem(word) for word in words]
    rows = articles_api.articles_containing(session, stems, limit=limit)
    # The longest word is the likeliest surname: quote the text around it.
    return _quotes_of(rows, [max(stems, key=len)], limit=limit)


@dataclass(frozen=True)
class EntityProfile:
    """What the group's own record says of the person beside the verdicts: the forms
    of the name the news wrote, the regions of the registry cards, the list entries the
    name matched. Two people in one group show here first."""

    key: str
    name: str
    gender: str | None
    # (form, mentions), most frequent first.
    variants: list[tuple[str, int]]
    # (region, publications), most named first.
    regions: list[tuple[str, int]]
    # (full name, birth date ISO) of the list entries matched.
    rf_entries: list[tuple[str, str]]


def entity_profile(session: Session, key: str) -> EntityProfile | None:
    group = identity.groups_by_key(session, [key]).get(key)
    if group is None:
        return None
    rf_entries = [
        (entry.full_name, entry.birth_date.isoformat())
        for entry in verdicts.list_entries(session, group.id)
    ]
    return EntityProfile(
        key=group.key,
        name=group.name,
        gender=group.gender,
        variants=[(str(form), int(count)) for form, count in group.variants or []],
        regions=[(str(region), int(count)) for region, count in group.regions or []],
        rf_entries=rf_entries,
    )

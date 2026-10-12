"""What the database holds of one person: the entity group, the verdicts on it and the
publications behind them. Read only; every row is what the pipeline wrote.

A person is an entity group (ADR 0024), named by its key. `persons` is not read.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Row, func, literal, select, text
from sqlalchemy.orm import InstrumentedAttribute, Session

from db.orm_models import (
    EntityGroupChargeRecord,
    EntityGroupNewsRecord,
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    EntityGroupRoleRecord,
)
from processing.evidence.publications import person_evidence_cte

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


# What parts a written name into words, besides a space. The same set is used in SQL
# and here, so both mean the same by «a word of the name».
_SEPARATORS = "-‐‑–—'’ʼ`\".,()«»\u00a0\t"
_TO_SPACES = str.maketrans(_SEPARATORS, " " * len(_SEPARATORS))


def name_words(name: str) -> list[str]:
    return [_fold(word) for word in _WORD.findall(name) if len(word) >= 2]


def _own_words(name: str) -> set[str]:
    """The words of a stored name, parted as `_spaced` parts them in SQL."""
    return set(_fold(name).translate(_TO_SPACES).split(" "))


def _spaced(column: InstrumentedAttribute[str]) -> ColumnElement[str]:
    """The name in SQL with a space before every word: « иван петров-водкин» becomes
    « иван петров водкин», so `LIKE '% stem%'` finds a word by its beginning."""
    folded = func.replace(func.lower(column), "ё", "е")
    return literal(" ").concat(func.translate(folded, _SEPARATORS, " " * len(_SEPARATORS)))


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
    spaced = _spaced(EntityGroupRecord.name)
    query = (
        select(
            EntityGroupRecord.key,
            EntityGroupRecord.name,
            EntityGroupRecord.mention_count,
            EntityGroupRecord.article_count,
            EntityGroupRecord.last_published_at,
        )
        .where(*(spaced.like(f"% {_stem(word)}%") for word in words))
        .order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key)
        .limit(limit)
    )
    return [
        EntityHit(
            key=row.key,
            name=row.name,
            mention_count=row.mention_count,
            article_count=row.article_count,
            last_published_at=row.last_published_at,
            exact=set(words) <= _own_words(row.name),
        )
        for row in session.execute(query)
    ]


def entity_facts(session: Session, key: str) -> list[StatedFact] | None:
    """What the steps decided of the person; None when no group has this key."""
    group = session.scalar(select(EntityGroupRecord).where(EntityGroupRecord.key == key))
    if group is None:
        return None
    facts: list[StatedFact] = []
    role = session.scalar(
        select(EntityGroupRoleRecord).where(EntityGroupRoleRecord.group_id == group.id)
    )
    if role is not None:
        value = role.role if role.kind is None else f"{role.role} ({role.kind})"
        facts.append(StatedFact("role", value, role.quote or role.reason))
    charges = sorted(
        set(
            session.scalars(
                select(EntityGroupChargeRecord.article).where(
                    EntityGroupChargeRecord.group_id == group.id
                )
            )
        )
    )
    if charges:
        facts.append(StatedFact("charges", ", ".join(charges), ""))
    politics = session.scalar(
        select(EntityGroupPoliticsRecord).where(EntityGroupPoliticsRecord.group_id == group.id)
    )
    if politics is not None:
        facts.append(StatedFact("politics", politics.verdict, politics.quote or politics.reason))
    levels = sorted(
        set(
            session.scalars(
                select(EntityGroupRfMatchRecord.level).where(
                    EntityGroupRfMatchRecord.group_id == group.id
                )
            )
        )
    )
    if levels:
        facts.append(StatedFact("rf_list", ", ".join(levels), ""))
    news = session.scalar(
        select(EntityGroupNewsRecord).where(EntityGroupNewsRecord.group_id == group.id)
    )
    if news is not None:
        facts.append(
            StatedFact("latest_news", news.kind, news.quote or news.reason, news.published_at)
        )
    return facts


_EVIDENCE = text(
    f"""
    WITH {person_evidence_cte()}
    SELECT e.article_id, e.title, e.canonical_url, e.source, e.published_at, e.quote,
           e.quote_start
    FROM person_evidence e
    -- The group as a scalar: its id is pushed into both branches of the evidence, so
    -- only this person's mentions are read. Joined instead, the whole evidence of
    -- every person was built and hashed first (150 ms on 1 600 articles, 2026-10-12).
    WHERE e.group_id = (SELECT g.id FROM entity_groups g WHERE g.key = :key)
    ORDER BY e.published_at DESC NULLS LAST, e.article_id DESC, e.quote_start
    LIMIT :limit
    """
)

_ARTICLE_TEXTS = text(
    f"""
    WITH {person_evidence_cte()}
    SELECT DISTINCT a.id AS article_id, a.title, d.canonical_url, s.name AS source,
           a.published_at, a.text
    FROM person_evidence e
    JOIN parsed_articles a ON a.id = e.article_id
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE e.group_id = (SELECT g.id FROM entity_groups g WHERE g.key = :key)
    ORDER BY a.published_at DESC NULLS LAST, a.id DESC
    LIMIT :articles
    """
)

_STORED_TEXTS = text(
    """
    SELECT a.id AS article_id, a.title, d.canonical_url, s.name AS source,
           a.published_at, a.text
    FROM parsed_articles a
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE replace(lower(a.text), 'ё', 'е') LIKE ALL (:patterns)
    ORDER BY a.published_at DESC NULLS LAST, a.id DESC
    LIMIT :articles
    """
)


def entity_evidence(session: Session, key: str, *, limit: int) -> list[PublicationQuote]:
    """The places the publications name the person, the latest first."""
    return [
        PublicationQuote(
            article_id=row.article_id,
            title=row.title or "",
            url=row.canonical_url,
            source=row.source,
            published_at=row.published_at,
            quote=row.quote or "",
            span_start=row.quote_start,
            span_end=row.quote_start + len(row.quote or ""),
        )
        for row in session.execute(
            _EVIDENCE, {"key": key, "context": QUOTE_CONTEXT, "limit": limit}
        )
    ]


def _quotes_of(
    rows: Sequence[Row[Any]], stems: Sequence[str], *, limit: int
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
                article_id=row.article_id,
                title=row.title or "",
                url=row.canonical_url,
                source=row.source,
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
    rows = session.execute(
        _ARTICLE_TEXTS, {"key": key, "context": QUOTE_CONTEXT, "articles": articles}
    ).all()
    return _quotes_of(rows, stems, limit=limit)


def stored_publication_quotes(session: Session, name: str, *, limit: int) -> list[PublicationQuote]:
    """The stored publications whose text holds every word of the name, whether or not
    an entity group was made of them: one quote per article, around the surname."""
    words = name_words(name)
    if not words:
        return []
    stems = [_stem(word) for word in words]
    rows = session.execute(
        _STORED_TEXTS, {"patterns": [f"%{stem}%" for stem in stems], "articles": limit}
    ).all()
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


_RF_ENTRIES = text(
    """
    SELECT DISTINCT e.full_name, e.birth_date::date AS birth_date
    FROM entity_group_rf_matches m
    JOIN rosfinmonitoring_entries e ON e.id = m.entry_id
    WHERE m.group_id = :group_id AND e.birth_date IS NOT NULL
    ORDER BY e.full_name, birth_date
    """
)


def entity_profile(session: Session, key: str) -> EntityProfile | None:
    group = session.scalar(select(EntityGroupRecord).where(EntityGroupRecord.key == key))
    if group is None:
        return None
    rf_entries = [
        (row.full_name, row.birth_date.isoformat())
        for row in session.execute(_RF_ENTRIES, {"group_id": group.id})
    ]
    return EntityProfile(
        key=group.key,
        name=group.name,
        gender=group.gender,
        variants=[(str(form), int(count)) for form, count in group.variants or []],
        regions=[(str(region), int(count)) for region, count in group.regions or []],
        rf_entries=rf_entries,
    )

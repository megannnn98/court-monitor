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

from sqlalchemy import Row, func, select, text
from sqlalchemy.orm import Session

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


def name_words(name: str) -> list[str]:
    return [_fold(word) for word in _WORD.findall(name) if len(word) >= 2]


def _stem(word: str) -> str:
    return word[: max(_MIN_STEM, len(word) - _ENDING)]


def find_entities(session: Session, name: str, *, limit: int) -> list[EntityHit]:
    """The entity groups whose name holds every word asked for, the most mentioned
    first. Words are matched by their beginning, so a declined name finds its person."""
    words = name_words(name)
    if not words:
        return []
    folded = func.replace(func.lower(EntityGroupRecord.name), "ё", "е")
    query = select(EntityGroupRecord).order_by(
        EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key
    )
    for word in words:
        query = query.where(folded.like(f"%{_stem(word)}%"))
    hits: list[EntityHit] = []
    # No LIMIT in SQL: a name that only holds the letters inside a word is dropped below,
    # and it must not take the place of a person further down.
    for group in session.scalars(query):
        if len(hits) >= limit:
            break
        own = set(name_words(group.name))
        if not all(any(part.startswith(_stem(word)) for part in own) for word in words):
            # The beginning was found inside another word of the name.
            continue
        hits.append(
            EntityHit(
                key=group.key,
                name=group.name,
                mention_count=group.mention_count,
                article_count=group.article_count,
                last_published_at=group.last_published_at,
                exact=set(words) <= own,
            )
        )
    return hits


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
    JOIN entity_groups g ON g.id = e.group_id
    WHERE g.key = :key
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
    JOIN entity_groups g ON g.id = e.group_id
    JOIN parsed_articles a ON a.id = e.article_id
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE g.key = :key
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

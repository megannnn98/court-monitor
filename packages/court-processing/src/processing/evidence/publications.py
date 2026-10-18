"""The publication evidence of a rebuilt entity group.

A group is tied to an article by a named mention (`entity_group_mentions`, the
processing's) or by an operator's identification of an unnamed quote
(`entity_group_unnamed_mentions`, the figurant itself being the analysis's, read through
`IdentityDownstream`). The article — its title, date, source, the words around the
mention — is the ingestion's, read through `ingestion.read` (ADR 0031). Every caller
asks for the groups or the articles it shows, and orders and counts the evidence itself.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.arrays import any_of, any_text
from ingestion import read
from processing.identity.downstream import IdentityDownstream
from processing.storage.models import (
    ArticleExtractionRunRecord,
    EntityGroupMentionRecord,
    EntityGroupUnnamedMentionRecord,
    EntityMentionRecord,
)


@dataclass(frozen=True)
class Evidence:
    """One place a publication names a group's person."""

    group_id: int
    article_id: int
    title: str
    published_at: datetime | None
    url: str
    source: str
    start_offset: int
    end_offset: int
    # The words around the mention (`context` characters each side), or the figurant's
    # quote; None for a mention when the caller did not ask for quotes (`quoted`).
    quote: str | None
    quote_start: int
    kind: Literal["mention", "unnamed_resolution"]
    resolution: str | None = None
    rf_name: str | None = None
    rf_birth_date: date | None = None
    decided_at: datetime | None = None


def person_evidence(
    session: Session,
    downstream: IdentityDownstream,
    *,
    groups: Collection[int] | None = None,
    articles: Collection[int] | None = None,
    context: int = 0,
    quotes: bool = True,
) -> list[Evidence]:
    """The evidence of the groups asked for, or of every group in the articles asked
    for; None for either is every one. In no particular order."""
    if (groups is not None and not groups) or (articles is not None and not articles):
        return []
    mention_query = (
        select(
            EntityGroupMentionRecord.group_id,
            ArticleExtractionRunRecord.article_id,
            EntityMentionRecord.start_offset,
            EntityMentionRecord.end_offset,
        )
        .join(EntityMentionRecord, EntityMentionRecord.id == EntityGroupMentionRecord.mention_id)
        .join(
            ArticleExtractionRunRecord,
            ArticleExtractionRunRecord.id == EntityMentionRecord.extraction_run_id,
        )
    )
    if groups is not None:
        mention_query = mention_query.where(
            any_of(EntityGroupMentionRecord.group_id, groups, name="groups")
        )
    if articles is not None:
        mention_query = mention_query.where(
            any_of(ArticleExtractionRunRecord.article_id, articles, name="articles")
        )
    mentions = [tuple(row) for row in session.execute(mention_query).all()]

    link_query = select(
        EntityGroupUnnamedMentionRecord.group_id, EntityGroupUnnamedMentionRecord.figurant_key
    )
    if groups is not None:
        link_query = link_query.where(
            any_of(EntityGroupUnnamedMentionRecord.group_id, groups, name="groups")
        )
    if articles is not None:
        # The articles' figurants first (the analysis's), then only their links: a page
        # of publications reads what it shows, not every unnamed quote of the corpus.
        figurants = downstream.figurants_in(session, articles)
        links: list[tuple[int, str]] = []
        if figurants:
            links = [
                (group_id, key)
                for group_id, key in session.execute(
                    link_query.where(
                        any_text(
                            EntityGroupUnnamedMentionRecord.figurant_key, figurants, name="keys"
                        )
                    )
                ).all()
            ]
    else:
        links = [(group_id, key) for group_id, key in session.execute(link_query).all()]
        figurants = downstream.figurants(session, {key for _, key in links})
    unnamed = [
        (group_id, figurants[key])
        for group_id, key in links
        if key in figurants and (articles is None or figurants[key].article_id in articles)
    ]

    cards = read.article_cards(
        session,
        {row[1] for row in mentions} | {figurant.article_id for _, figurant in unnamed},
    )
    mentions = [row for row in mentions if row[1] in cards]
    texts: list[str | None] = [None] * len(mentions)
    if quotes:
        texts = read.text_windows(
            session,
            [
                read.TextWindow(
                    article_id,
                    max(start - context, 0),
                    end - max(start - context, 0) + context,
                )
                for _, article_id, start, end in mentions
            ],
        )
    found = [
        Evidence(
            group_id=group_id,
            article_id=article_id,
            title=cards[article_id].title,
            published_at=cards[article_id].published_at,
            url=cards[article_id].url,
            source=cards[article_id].source_name,
            start_offset=start,
            end_offset=end,
            quote=quote,
            quote_start=max(start - context, 0),
            kind="mention",
        )
        for (group_id, article_id, start, end), quote in zip(mentions, texts, strict=True)
    ]
    found += [
        Evidence(
            group_id=group_id,
            article_id=figurant.article_id,
            title=cards[figurant.article_id].title,
            published_at=cards[figurant.article_id].published_at,
            url=cards[figurant.article_id].url,
            source=cards[figurant.article_id].source_name,
            start_offset=figurant.start_offset,
            end_offset=figurant.end_offset,
            quote=figurant.quote,
            quote_start=figurant.start_offset,
            kind="unnamed_resolution",
            resolution=figurant.resolution,
            rf_name=figurant.rf_name,
            rf_birth_date=figurant.rf_birth_date,
            decided_at=figurant.decided_at,
        )
        for group_id, figurant in unnamed
        if figurant.article_id in cards
    ]
    return found


def quoted(session: Session, evidence: list[Evidence], context: int) -> list[Evidence]:
    """The evidence with the words around each mention: for a caller that picked a few
    places from evidence read without quotes."""
    mentions = [item for item in evidence if item.kind == "mention"]
    texts = read.text_windows(
        session,
        [
            read.TextWindow(
                item.article_id,
                max(item.start_offset - context, 0),
                item.end_offset - max(item.start_offset - context, 0) + context,
            )
            for item in mentions
        ],
    )
    by_place = {
        (item.group_id, item.article_id, item.start_offset, item.end_offset): text
        for item, text in zip(mentions, texts, strict=True)
    }
    return [
        replace(
            item,
            quote=by_place.get(
                (item.group_id, item.article_id, item.start_offset, item.end_offset)
            ),
            quote_start=max(item.start_offset - context, 0),
        )
        if item.kind == "mention"
        else item
        for item in evidence
    ]


# Stands in for an unknown date in a sort key; a flag before it puts those last.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def latest_first(published_at: datetime | None) -> tuple[bool, float]:
    """A sort key: the latest publication first, an unknown date last (`DESC NULLS LAST`)."""
    return published_at is None, -(published_at or _EPOCH).timestamp()


def latest_evidence(
    session: Session,
    groups: Collection[int],
    downstream: IdentityDownstream,
    *,
    context: int,
    per_group: int,
) -> list[Evidence]:
    """Per group, its latest `per_group` publications, each by its first mention there
    with `context` characters around it: in group order, the latest publication first."""
    evidence = person_evidence(session, downstream, groups=groups, quotes=False)
    latest = latest_publications(evidence, per_group)
    return quoted(session, [item for group in sorted(latest) for item in latest[group]], context)


def latest_publications(evidence: Iterable[Evidence], per_group: int) -> dict[int, list[Evidence]]:
    """Per group, its latest `per_group` publications, each by its first place in the
    text: the latest first (unknown dates last), then the higher article id."""
    first: dict[tuple[int, int], Evidence] = {}
    for item in evidence:
        known = first.get((item.group_id, item.article_id))
        if known is None or item.start_offset < known.start_offset:
            first[(item.group_id, item.article_id)] = item
    by_group: dict[int, list[Evidence]] = defaultdict(list)
    for item in first.values():
        by_group[item.group_id].append(item)
    return {
        group_id: sorted(
            items, key=lambda item: (*latest_first(item.published_at), -item.article_id)
        )[:per_group]
        for group_id, items in by_group.items()
    }

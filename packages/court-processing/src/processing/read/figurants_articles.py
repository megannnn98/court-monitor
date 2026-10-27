"""Figurants, charges and publication evidence."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from db.arrays import any_of
from ingestion import read
from processing.storage.models import (
    ArticleDigestAnswerRecord,
    EntityGroupChargeRecord,
    JunkScreenHoldRecord,
)


def held_article_count(session: Session) -> int:
    """Every held publication counts, including those a model already read as junk."""
    return (
        session.scalar(
            select(func.count())
            .select_from(JunkScreenHoldRecord)
            .where(JunkScreenHoldRecord.status == "held")
        )
        or 0
    )


@dataclass(frozen=True)
class Charge:
    """A Criminal Code article an event ties to a group (step 3)."""

    group_id: int
    event_id: int
    publication_id: int
    article: str
    part: str | None
    clause: str | None
    event_type: str
    other_targets: int
    quote: str


def charges(session: Session, ids: Collection[int]) -> dict[int, list[Charge]]:
    """Per group asked for, its charges in id order."""
    if not ids:
        return {}
    found: dict[int, list[Charge]] = {}
    for record in session.scalars(
        select(EntityGroupChargeRecord)
        .where(any_of(EntityGroupChargeRecord.group_id, ids))
        .order_by(EntityGroupChargeRecord.id)
    ):
        found.setdefault(record.group_id, []).append(
            Charge(
                group_id=record.group_id,
                event_id=record.event_id,
                publication_id=record.publication_id,
                article=record.article,
                part=record.part,
                clause=record.clause,
                event_type=record.event_type,
                other_targets=record.other_targets,
                quote=record.quote,
            )
        )
    return found


def charge_articles(session: Session, ids: Collection[int]) -> dict[int, list[tuple[str, bool]]]:
    """Per group, its Criminal Code articles, each with whether any of its events names
    the group alone (`other_targets` = 0); in no order."""
    if not ids:
        return {}
    rows = session.execute(
        select(
            EntityGroupChargeRecord.group_id,
            EntityGroupChargeRecord.article,
            func.bool_or(EntityGroupChargeRecord.other_targets == 0),
        )
        .where(any_of(EntityGroupChargeRecord.group_id, ids))
        .group_by(EntityGroupChargeRecord.group_id, EntityGroupChargeRecord.article)
    ).all()
    found: dict[int, list[tuple[str, bool]]] = {}
    for group_id, article, sole in rows:
        found.setdefault(group_id, []).append((article, bool(sole)))
    return found


# A card of the «Мемориал» registry names its category: «реестр преследуемых: «…»».
_MEMORIAL_MARK = "реестр преследуемых: «"


_MEMORIAL_CATEGORY = "реестр преследуемых: «([^»]+)»"


_GROUP_ARTICLES = text(
    """
    SELECT DISTINCT gm.group_id, r.article_id
    FROM entity_group_mentions gm
    JOIN entity_mentions m ON m.id = gm.mention_id
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    WHERE gm.group_id = ANY(:groups)
    """
)


def memorial_categories(session: Session, ids: Collection[int]) -> dict[int, str | None]:
    """Per group named on a «Мемориал» registry card, the category of its latest such
    card (None: the card's words could not be read)."""
    if not ids:
        return {}
    pairs = session.execute(_GROUP_ARTICLES, {"groups": sorted(set(ids))}).all()
    articles = {article_id for _, article_id in pairs}
    cards = read.texts_with(session, articles, _MEMORIAL_MARK)
    published = read.published_at(session, cards)
    categories = read.text_pattern(session, cards, _MEMORIAL_CATEGORY)
    latest: dict[int, int] = {}
    for group_id, article_id in pairs:
        if article_id not in cards:
            continue
        known = latest.get(group_id)
        if known is None or _later(published[article_id], published[known]):
            latest[group_id] = article_id
    return {group_id: categories.get(article_id) for group_id, article_id in latest.items()}


def _later(first: datetime | None, second: datetime | None) -> bool:
    """`first` comes before `second` in `published_at DESC NULLS LAST`."""
    return first is not None and (second is None or first > second)


def digest_relevance(session: Session, article_ids: Collection[int]) -> dict[int, bool | None]:
    """Per article the digest step read, whether it is about its people (None: the model
    said nothing yet); an article not read is left out."""
    if not article_ids:
        return {}
    return {
        article_id: relevant
        for article_id, relevant in session.execute(
            select(ArticleDigestAnswerRecord.article_id, ArticleDigestAnswerRecord.relevant).where(
                any_of(ArticleDigestAnswerRecord.article_id, article_ids)
            )
        ).all()
    }


@dataclass(frozen=True)
class ArticlePerson:
    """A group named in an article: how often there, and the charges it is told of there."""

    key: str
    name: str
    article_id: int
    mentions: int
    charges: list[str]


_PEOPLE_IN = text(
    """
    SELECT g.key, g.name, r.article_id, count(*) AS mentions,
           coalesce(
               (SELECT array_agg(DISTINCT c.article ORDER BY c.article)
                FROM entity_group_charges c
                WHERE c.group_id = g.id AND c.publication_id = r.article_id),
               ARRAY[]::varchar[]) AS charges
    FROM entity_group_mentions gm
    JOIN entity_groups g ON g.id = gm.group_id
    JOIN entity_mentions m ON m.id = gm.mention_id
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    WHERE r.article_id = ANY(:article_ids)
    GROUP BY g.id, g.key, g.name, r.article_id
    ORDER BY mentions DESC, g.key
    LIMIT :limit
    """
)


def people_in(session: Session, article_ids: Collection[int], *, limit: int) -> list[ArticlePerson]:
    """The groups named in these articles, by group and article: the most mentioned first,
    then by key (in the database's collation), at most `limit`."""
    if not article_ids:
        return []
    rows = session.execute(_PEOPLE_IN, {"article_ids": list(article_ids), "limit": limit})
    return [
        ArticlePerson(row.key, row.name, row.article_id, row.mentions, list(row.charges or []))
        for row in rows
    ]


@dataclass(frozen=True)
class FigurantProfile:
    """A figurant group with the articles it is charged under."""

    id: int
    key: str
    name: str
    role_method: str
    # Every article charged, and those of events with no other accused: the person's
    # own beyond doubt. Both sorted, each once.
    articles: list[str]
    own_articles: list[str]


_FIGURANT_PROFILES = text(
    """
    SELECT g.id, g.key, g.name, r.method AS role_method,
           coalesce((SELECT array_agg(DISTINCT c.article ORDER BY c.article)
                     FROM entity_group_charges c WHERE c.group_id = g.id), '{}') AS articles,
           coalesce((SELECT array_agg(DISTINCT c.article ORDER BY c.article)
                     FROM entity_group_charges c
                     WHERE c.group_id = g.id AND c.other_targets = 0), '{}') AS own_articles
    FROM entity_groups g
    JOIN entity_group_roles r ON r.group_id = g.id AND r.role = 'figurant'
    ORDER BY g.id
    """
)


def figurant_profiles(session: Session) -> list[FigurantProfile]:
    """Every figurant group, in id order (the articles sorted in the database's collation)."""
    return [
        FigurantProfile(row.id, row.key, row.name, row.role_method, row.articles, row.own_articles)
        for row in session.execute(_FIGURANT_PROFILES)
    ]


_FIRST_CHARGE_QUOTES = text(
    """
    SELECT DISTINCT ON (group_id) group_id, article, quote
    FROM entity_group_charges
    WHERE group_id = ANY(:groups) AND article = ANY(:articles)
    ORDER BY group_id, other_targets, id
    """
)


def first_charge_quotes(
    session: Session, ids: Collection[int], articles: Sequence[str]
) -> dict[int, tuple[str, str]]:
    """Per group, the first charge under one of `articles` (the fewest other accused
    first): its article and its quote."""
    return {
        group_id: (article, quote)
        for group_id, article, quote in session.execute(
            _FIRST_CHARGE_QUOTES, {"groups": list(ids), "articles": list(articles)}
        ).all()
    }


@dataclass(frozen=True)
class FigurantMention:
    """A figurant group's mention that is a party to an event, in an article."""

    key: str
    name: str
    gender: str | None
    article_id: int
    start_offset: int
    end_offset: int


_FIGURANT_MENTIONS = text(
    """
    SELECT g.key, g.name, g.gender, r.article_id, m.start_offset, m.end_offset
    FROM article_extraction_runs r
    JOIN entity_mentions m ON m.extraction_run_id = r.id
    JOIN entity_group_mentions gm ON gm.mention_id = m.id
    JOIN entity_groups g ON g.id = gm.group_id
    JOIN entity_group_roles ro ON ro.group_id = g.id AND ro.role = 'figurant'
    WHERE r.article_id = ANY(:articles)
      AND EXISTS (
          SELECT 1 FROM event_entity_mentions em
          JOIN extracted_events e ON e.id = em.event_id
          WHERE em.mention_id = m.id AND (:any_event OR e.event_type = ANY(:events))
      )
    """
)


def figurant_mentions(
    session: Session, article_ids: Collection[int], event_types: Collection[str] | None
) -> list[FigurantMention]:
    """The figurants' mentions in these articles that take part in an event of these
    types (of any type for None). In no particular order."""
    if not article_ids:
        return []
    rows = session.execute(
        _FIGURANT_MENTIONS,
        {
            "articles": list(article_ids),
            "any_event": event_types is None,
            "events": list(event_types or ()),
        },
    )
    return [FigurantMention(*row) for row in rows]


_KEYED_CHARGES = text(
    """
    SELECT g.key, c.publication_id, c.article
    FROM entity_group_charges c JOIN entity_groups g ON g.id = c.group_id
    WHERE g.key = ANY(:keys) AND c.publication_id = ANY(:publications)
    """
)


def publication_charges(
    session: Session, keys: Collection[str], publications: Collection[int]
) -> dict[tuple[str, int], set[str]]:
    """The articles of the Code each group (by key) is charged under in each publication."""
    found: dict[tuple[str, int], set[str]] = {}
    if not keys or not publications:
        return found
    for key, publication, article in session.execute(
        _KEYED_CHARGES, {"keys": sorted(keys), "publications": sorted(publications)}
    ):
        found.setdefault((key, publication), set()).add(article)
    return found

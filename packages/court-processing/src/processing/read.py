"""The entity groups and their roles as the stages after processing read them (ADR 0031).

The tables are the processing's (`processing.storage`); another package reads them here,
in its own transaction, and gets values, never ORM records. Many ids at once: a page or
a step asks once for what it shows. The groups' publication evidence is
`processing.evidence.publications`.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from sqlalchemy import ColumnElement, Text, cast, exists, func, literal, or_, select, text
from sqlalchemy.orm import InstrumentedAttribute, Session

from db.arrays import any_of, any_text
from ingestion import read
from processing.identity.disputes import EntityRef
from processing.storage.models import (
    ArticleDigestAnswerRecord,
    ArticleSentenceReadingRecord,
    ArticleSentenceRecord,
    EntityGroupChargeRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
    EntityMentionRecord,
    EventEntityMentionRecord,
    JunkScreenHoldRecord,
)


class GroupIdentity(Protocol):
    """Stable identity required when applying a decision, without an ORM dependency."""

    @property
    def id(self) -> int: ...

    @property
    def key(self) -> str: ...


def group_references(session: Session) -> list[EntityRef]:
    """Lightweight references for identity comparisons, with no evidence payloads."""
    return [
        EntityRef(id=id_, key=key, name=name, mention_count=count)
        for id_, key, name, count in session.execute(
            select(
                EntityGroupRecord.id,
                EntityGroupRecord.key,
                EntityGroupRecord.name,
                EntityGroupRecord.mention_count,
            )
        )
    ]


def references_by_mentions(
    session: Session, ids: Collection[int], *, limit: int
) -> list[EntityRef]:
    """Review candidates sorted by frequency and database key collation, under LIMIT."""
    if not ids:
        return []
    return [
        EntityRef(id=id_, key=key, name=name, mention_count=count)
        for id_, key, name, count in session.execute(
            select(
                EntityGroupRecord.id,
                EntityGroupRecord.key,
                EntityGroupRecord.name,
                EntityGroupRecord.mention_count,
            )
            .where(any_of(EntityGroupRecord.id, ids))
            .order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key)
            .limit(limit)
        )
    ]


def role_review(session: Session, *, role: str, limit: int) -> list[tuple[str, str, str]]:
    """(key, name, reason) for one role's review queue, most mentioned first."""
    return [
        (key, name, reason)
        for key, name, reason in session.execute(
            select(EntityGroupRecord.key, EntityGroupRecord.name, EntityGroupRoleRecord.reason)
            .join(EntityGroupRoleRecord, EntityGroupRoleRecord.group_id == EntityGroupRecord.id)
            .where(EntityGroupRoleRecord.role == role)
            .order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key)
            .limit(limit)
        )
    ]


def group_count(session: Session) -> int:
    """All current entity groups, whatever their role or classification."""
    return session.scalar(select(func.count()).select_from(EntityGroupRecord)) or 0


def role_count(session: Session, *, role: str) -> int:
    """Groups assigned the given role by the roles step or the operator."""
    return (
        session.scalar(
            select(func.count())
            .select_from(EntityGroupRoleRecord)
            .where(EntityGroupRoleRecord.role == role)
        )
        or 0
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
class HiddenSentence:
    """A sentence removed from all result counts, with its publication title."""

    id: int
    article_id: int
    person: str
    region: str
    kind: str
    months: int
    fine_rub: int
    in_absentia: bool
    sentenced_on: str
    reason: str
    reason_text: str
    quote: str
    title: str


def hidden_sentences(session: Session) -> list[HiddenSentence]:
    """Hidden evidence newest first, enriched by one bulk publication read."""
    rows = session.execute(
        select(
            ArticleSentenceRecord.id,
            ArticleSentenceRecord.article_id,
            ArticleSentenceRecord.person,
            ArticleSentenceRecord.region,
            ArticleSentenceRecord.kind,
            ArticleSentenceRecord.months,
            ArticleSentenceRecord.fine_rub,
            ArticleSentenceRecord.in_absentia,
            ArticleSentenceRecord.sentenced_on,
            ArticleSentenceRecord.reason,
            ArticleSentenceRecord.reason_text,
            ArticleSentenceRecord.quote,
        )
        .where(ArticleSentenceRecord.hidden)
        .order_by(ArticleSentenceRecord.id.desc())
    ).all()
    cards = read.article_cards(session, {row.article_id for row in rows})
    return [
        HiddenSentence(
            id=row.id,
            article_id=row.article_id,
            person=row.person,
            region=row.region,
            kind=row.kind,
            months=row.months,
            fine_rub=row.fine_rub,
            in_absentia=row.in_absentia,
            sentenced_on=row.sentenced_on,
            reason=row.reason,
            reason_text=row.reason_text,
            quote=row.quote,
            title=cards[row.article_id].title,
        )
        for row in rows
        if row.article_id in cards
    ]


@dataclass(frozen=True)
class ShownSentence:
    """A sentence the result counts, with the publication it stands in."""

    id: int
    article_id: int
    person: str
    person_key: str | None
    region: str
    kind: str
    months: int
    fine_rub: int
    in_absentia: bool
    sentenced_on: str
    articles: list[Any]
    reason: str
    reason_text: str
    quote: str
    title: str
    published_at: datetime | None
    source: str


def shown_sentences(session: Session) -> list[ShownSentence]:
    """Every sentence not hidden, in id order, enriched by one bulk publication read."""
    rows = session.execute(
        select(
            ArticleSentenceRecord.id,
            ArticleSentenceRecord.article_id,
            ArticleSentenceRecord.person,
            ArticleSentenceRecord.person_key,
            ArticleSentenceRecord.region,
            ArticleSentenceRecord.kind,
            ArticleSentenceRecord.months,
            ArticleSentenceRecord.fine_rub,
            ArticleSentenceRecord.in_absentia,
            ArticleSentenceRecord.sentenced_on,
            ArticleSentenceRecord.articles,
            ArticleSentenceRecord.reason,
            ArticleSentenceRecord.reason_text,
            ArticleSentenceRecord.quote,
        )
        .where(~ArticleSentenceRecord.hidden)
        .order_by(ArticleSentenceRecord.id)
    ).all()
    cards = read.article_cards(session, {row.article_id for row in rows})
    return [
        ShownSentence(
            id=row.id,
            article_id=row.article_id,
            person=row.person,
            person_key=row.person_key,
            region=row.region,
            kind=row.kind,
            months=row.months,
            fine_rub=row.fine_rub,
            in_absentia=row.in_absentia,
            sentenced_on=row.sentenced_on,
            articles=row.articles,
            reason=row.reason,
            reason_text=row.reason_text,
            quote=row.quote,
            title=cards[row.article_id].title,
            published_at=cards[row.article_id].published_at,
            source=cards[row.article_id].source_name,
        )
        for row in rows
        if row.article_id in cards
    ]


def sentence_reading_count(session: Session) -> int:
    """Publications the sentence-reading step has processed."""
    return session.scalar(select(func.count()).select_from(ArticleSentenceReadingRecord)) or 0


@dataclass(frozen=True)
class Group:
    """An entity group as the rebuild left it."""

    id: int
    key: str
    name: str
    mention_count: int
    article_count: int
    last_published_at: datetime | None
    gender: str | None
    name_source: str
    collected_at: datetime
    # (form, count) of the name as written, most frequent first.
    variants: tuple[tuple[Any, ...], ...] = ()
    # (region, count), most frequent first.
    regions: tuple[tuple[Any, ...], ...] = ()
    event_types: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Role:
    """What step 4, or a person, decided a group is in its cases."""

    group_id: int
    role: str
    kind: str | None
    method: str
    reason: str
    quote: str


def _group(record: EntityGroupRecord) -> Group:
    return Group(
        id=record.id,
        key=record.key,
        name=record.name,
        mention_count=record.mention_count,
        article_count=record.article_count,
        last_published_at=record.last_published_at,
        gender=record.gender,
        name_source=record.name_source,
        collected_at=record.collected_at,
        variants=tuple(tuple(item) for item in record.variants or ()),
        regions=tuple(tuple(item) for item in record.regions or ()),
        event_types=dict(record.event_types or {}),
    )


def groups(session: Session, ids: Collection[int] | None = None) -> dict[int, Group]:
    """The groups asked for (None: every one), in id order."""
    if ids is not None and not ids:
        return {}
    query = select(EntityGroupRecord).order_by(EntityGroupRecord.id)
    if ids is not None:
        query = query.where(any_of(EntityGroupRecord.id, ids))
    return {record.id: _group(record) for record in session.scalars(query)}


def groups_by_key(session: Session, keys: Collection[str]) -> dict[str, Group]:
    """The groups of the keys asked for; a key no group has is left out."""
    if not keys:
        return {}
    return {
        record.key: _group(record)
        for record in session.scalars(
            select(EntityGroupRecord).where(any_text(EntityGroupRecord.key, keys, name="keys"))
        )
    }


def roles(session: Session, ids: Collection[int] | None = None) -> dict[int, Role]:
    """The roles of the groups asked for (None: every group with one)."""
    if ids is not None and not ids:
        return {}
    query = select(EntityGroupRoleRecord)
    if ids is not None:
        query = query.where(any_of(EntityGroupRoleRecord.group_id, ids))
    return {
        record.group_id: Role(
            group_id=record.group_id,
            role=record.role,
            kind=record.kind,
            method=record.method,
            reason=record.reason,
            quote=record.quote,
        )
        for record in session.scalars(query)
    }


def groups_with_role(session: Session, role: str) -> list[int]:
    """The groups of one role («figurant», «mentioned», …), in id order."""
    return list(
        session.scalars(
            select(EntityGroupRoleRecord.group_id)
            .where(EntityGroupRoleRecord.role == role)
            .order_by(EntityGroupRoleRecord.group_id)
        )
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


# What parts a written name into words, besides a space (`groups_named_like`).
NAME_SEPARATORS = "-‐‑–—'’ʼ`\".,()«» \t"


def _spaced(column: InstrumentedAttribute[str]) -> ColumnElement[str]:
    """The name in SQL with a space before every word: « иван петров-водкин» becomes
    « иван петров водкин», so `LIKE '% stem%'` finds a word by its beginning."""
    folded = func.replace(func.lower(column), "ё", "е")
    return literal(" ").concat(func.translate(folded, NAME_SEPARATORS, " " * len(NAME_SEPARATORS)))


@dataclass(frozen=True)
class GroupHit:
    """A group found by its name: what a list of hits shows, no JSON of the group."""

    key: str
    name: str
    mention_count: int
    article_count: int
    last_published_at: datetime | None


def groups_named_like(session: Session, stems: Sequence[str], *, limit: int) -> list[GroupHit]:
    """The groups with a word of the name beginning with each stem (folded: lower case,
    «ё» as «е»), the most mentioned first. The whole filter runs in SQL, under its
    LIMIT, and only the columns of a hit are read."""
    if not stems:
        return []
    spaced = _spaced(EntityGroupRecord.name)
    return [
        GroupHit(*row)
        for row in session.execute(
            select(
                EntityGroupRecord.key,
                EntityGroupRecord.name,
                EntityGroupRecord.mention_count,
                EntityGroupRecord.article_count,
                EntityGroupRecord.last_published_at,
            )
            .where(*(spaced.like(f"% {stem}%") for stem in stems))
            .order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key)
            .limit(limit)
        ).all()
    ]


@dataclass(frozen=True)
class TargetEvent:
    """An extracted event whose target is one of a group's mentions."""

    id: int
    event_type: str
    event_date: datetime | None
    confidence: float | None
    extractor_name: str
    start_offset: int
    end_offset: int
    article_id: int


_TARGET_EVENTS = text(
    """
    SELECT DISTINCT e.id, e.event_type, e.event_date, e.confidence, e.extractor_name,
           e.start_offset, e.end_offset, r.article_id
    FROM entity_group_mentions gm
    JOIN event_entity_mentions em ON em.mention_id = gm.mention_id AND em.role = 'target'
    JOIN extracted_events e ON e.id = em.event_id
    JOIN article_extraction_runs r ON r.id = e.extraction_run_id
    WHERE gm.group_id = :group
    """
)


def target_events(session: Session, group_id: int) -> list[TargetEvent]:
    """Every event a group's mention is the target of, each once; in no order."""
    return [TargetEvent(*row) for row in session.execute(_TARGET_EVENTS, {"group": group_id})]


def event_orgs(session: Session, event_ids: Collection[int]) -> list[tuple[int, str, str]]:
    """(event, role, as the text wrote it) of the courts and bodies the events name."""
    if not event_ids:
        return []
    return [
        (event_id, role, surface)
        for event_id, role, surface in session.execute(
            select(
                EventEntityMentionRecord.event_id,
                EventEntityMentionRecord.role,
                EntityMentionRecord.surface_text,
            )
            .join(
                EntityMentionRecord, EntityMentionRecord.id == EventEntityMentionRecord.mention_id
            )
            .where(
                any_of(EventEntityMentionRecord.event_id, event_ids),
                EventEntityMentionRecord.role.in_(("court", "authority")),
            )
        ).all()
    ]


def by_name(session: Session, ids: Collection[int]) -> list[int]:
    """The groups asked for, by name as the database orders it (its collation), then key."""
    if not ids:
        return []
    return list(
        session.scalars(
            select(EntityGroupRecord.id)
            .where(any_of(EntityGroupRecord.id, ids))
            .order_by(EntityGroupRecord.name, EntityGroupRecord.key)
        )
    )


_WITH_EVENTS = text(
    """
    SELECT DISTINCT r.article_id FROM article_extraction_runs r
    JOIN extracted_events e ON e.extraction_run_id = r.id
    WHERE r.status = 'succeeded' AND e.event_type = ANY(:types)
    ORDER BY r.article_id
    """
)


def articles_with_events(session: Session, event_types: Collection[str]) -> list[int]:
    """The articles a successful extraction found an event of these types in, by id."""
    return list(session.scalars(_WITH_EVENTS, {"types": list(event_types)}))


_EVENT_COUNTS = text(
    """
    SELECT r.article_id, e.event_type, count(*)
    FROM extracted_events e JOIN article_extraction_runs r ON r.id = e.extraction_run_id
    WHERE r.article_id = ANY(:articles) AND r.status = 'succeeded'
    GROUP BY r.article_id, e.event_type
    """
)


def event_counts(session: Session, article_ids: Collection[int]) -> list[tuple[int, str, int]]:
    """(article, event type, how many) of the articles' successful extractions."""
    if not article_ids:
        return []
    return [
        (row[0], row[1], row[2])
        for row in session.execute(_EVENT_COUNTS, {"articles": sorted(set(article_ids))})
    ]


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


def latest_first_order(session: Session, ids: Collection[int]) -> list[int]:
    """The groups asked for, the latest publication first (none known last), then by key
    as the database orders it."""
    if not ids:
        return []
    return list(
        session.scalars(
            select(EntityGroupRecord.id)
            .where(any_of(EntityGroupRecord.id, ids))
            .order_by(
                EntityGroupRecord.last_published_at.desc().nulls_last(), EntityGroupRecord.key
            )
        )
    )


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
class EventFact:
    """An extracted event, with the day its own date names (None: the text gave none)."""

    id: int
    event_type: str
    event_date: datetime | None
    confidence: float
    start_offset: int
    end_offset: int
    article_id: int
    # `event_date` as a day of the database's time zone.
    event_day: date | None


_EVENT_FACTS = text(
    """
    SELECT e.id, e.event_type, e.event_date, e.confidence, e.start_offset, e.end_offset,
           r.article_id, e.event_date::date
    FROM extracted_events e JOIN article_extraction_runs r ON r.id = e.extraction_run_id
    WHERE e.id = ANY(:events)
    """
)
_EVENTS_NAMING = text(
    """
    SELECT em.event_id
    FROM entity_group_mentions gm
    JOIN event_entity_mentions em ON em.mention_id = gm.mention_id
    WHERE gm.group_id = :group
    """
)
_EVENTS_DATED = text(
    """
    SELECT e.id FROM extracted_events e
    JOIN unnest(CAST(:types AS text[]), CAST(:days AS date[])) AS k(event_type, day)
      ON k.event_type = e.event_type AND k.day = e.event_date::date
    """
)
_EVENTS_UNDATED = text(
    """
    SELECT e.id FROM extracted_events e
    JOIN article_extraction_runs r ON r.id = e.extraction_run_id
    WHERE e.event_date IS NULL AND r.article_id = ANY(:articles) AND e.event_type = ANY(:types)
    """
)
_EVENT_PEOPLE = text(
    """
    SELECT e.id, gm.group_id, em.role, coalesce(ro.role <> 'mentioned', true) AS binds
    FROM extracted_events e
    LEFT JOIN event_entity_mentions em ON em.event_id = e.id
    LEFT JOIN entity_group_mentions gm ON gm.mention_id = em.mention_id
    LEFT JOIN entity_group_roles ro ON ro.group_id = gm.group_id
    WHERE e.id = ANY(:events)
    """
)
# Everything the reports of an event name, with the person a name was resolved to (if it
# was).
_EVENT_MENTIONS = text(
    """
    SELECT em.role, m.entity_type, m.surface_text, m.normalized_text, m.normalized_data,
           g.id AS group_id, g.key, g.name
    FROM event_entity_mentions em
    JOIN entity_mentions m ON m.id = em.mention_id
    LEFT JOIN entity_group_mentions gm ON gm.mention_id = m.id
    LEFT JOIN entity_groups g ON g.id = gm.group_id
    WHERE em.event_id = ANY(:events)
    ORDER BY em.role, em.event_id, m.start_offset, m.id
    """
)


@dataclass(frozen=True)
class EventPerson:
    """Who an event names (a group, when the name was resolved to one) and as what;
    `binds`: the group is more than mentioned in its cases."""

    event_id: int
    group_id: int | None
    role: str | None
    binds: bool | None


@dataclass(frozen=True)
class EventMention:
    """A mention an event names, with the group it was resolved to (if it was)."""

    role: str
    entity_type: str
    surface_text: str | None
    normalized_text: str | None
    normalized_data: Any
    group_id: int | None
    key: str | None
    name: str | None


def event_facts(session: Session, ids: Collection[int]) -> dict[int, EventFact]:
    if not ids:
        return {}
    return {
        row[0]: EventFact(*row)
        for row in session.execute(_EVENT_FACTS, {"events": sorted(set(ids))}).all()
    }


def events_naming(session: Session, group_id: int) -> list[int]:
    """The events one of the group's mentions takes a part in, whatever the part."""
    return list(session.scalars(_EVENTS_NAMING, {"group": group_id}))


def events_dated(session: Session, keys: Collection[tuple[str, date]]) -> list[int]:
    """The events of these (type, day), by the day their own date names."""
    if not keys:
        return []
    ordered = sorted(keys)
    return list(
        session.scalars(
            _EVENTS_DATED,
            {"types": [key[0] for key in ordered], "days": [key[1] for key in ordered]},
        )
    )


def events_undated(
    session: Session, article_ids: Collection[int], types: Collection[str]
) -> list[int]:
    """The events without a date of their own, of these types, in these articles."""
    if not article_ids or not types:
        return []
    return list(
        session.scalars(
            _EVENTS_UNDATED, {"articles": sorted(set(article_ids)), "types": sorted(set(types))}
        )
    )


def event_people(session: Session, ids: Collection[int]) -> list[EventPerson]:
    """For each event, who it names (a row with no group when a name was not resolved,
    and one with nothing when it names nobody)."""
    if not ids:
        return []
    return [
        EventPerson(*row) for row in session.execute(_EVENT_PEOPLE, {"events": sorted(set(ids))})
    ]


def event_mentions(session: Session, ids: Collection[int]) -> list[EventMention]:
    """Everything the events name, by role, event, place in the text."""
    if not ids:
        return []
    return [
        EventMention(*row) for row in session.execute(_EVENT_MENTIONS, {"events": sorted(set(ids))})
    ]


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


# -- «Все люди»: the groups under the list's filters ---------------------------------


def filtered_group_ids(session: Session, *, q: str, article: str, region: str) -> list[int]:
    """The groups whose name or written form holds `q`, charged under `article`, of a
    region holding `region` (case-insensitive; an empty filter does not narrow)."""
    query = select(EntityGroupRecord.id)
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(
                EntityGroupRecord.name.ilike(pattern),
                cast(EntityGroupRecord.variants, Text).ilike(pattern),
            )
        )
    if article:
        query = query.where(
            exists().where(
                EntityGroupChargeRecord.group_id == EntityGroupRecord.id,
                EntityGroupChargeRecord.article == article,
            )
        )
    if region:
        query = query.where(cast(EntityGroupRecord.regions, Text).ilike(f"%{region}%"))
    return list(session.scalars(query))


def any_roles(session: Session) -> bool:
    """Whether step 4 has given anybody a role."""
    return bool(session.scalar(select(exists().select_from(EntityGroupRoleRecord))))


_ORDERS: dict[str, tuple[ColumnElement[Any], ...]] = {
    "mentions": (EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key.asc()),
    "articles": (EntityGroupRecord.article_count.desc(), EntityGroupRecord.key.asc()),
    "recent": (
        EntityGroupRecord.last_published_at.desc().nulls_last(),
        EntityGroupRecord.key.asc(),
    ),
}
GROUP_ORDERS = tuple(_ORDERS)


def ordered_groups(
    session: Session,
    ids: Collection[int] | None,
    *,
    order: str,
    offset: int = 0,
    limit: int | None = None,
) -> list[Group]:
    """A page of the groups asked for (None: every group), in one of `GROUP_ORDERS` (the
    key, the tie-break, in the database's collation)."""
    if ids is not None and not ids:
        return []
    query = select(EntityGroupRecord).order_by(*_ORDERS[order]).offset(offset).limit(limit)
    if ids is not None:
        query = query.where(any_of(EntityGroupRecord.id, ids))
    return [_group(record) for record in session.scalars(query)]


def all_regions(session: Session) -> list[str]:
    """Every region a group is of, each once, in the database's collation."""
    return list(
        session.scalars(
            text(
                "SELECT DISTINCT region->>0 FROM entity_groups, "
                "jsonb_array_elements(regions) AS region ORDER BY 1"
            )
        )
    )


def groups_named(session: Session, part: str, *, limit: int) -> list[Group]:
    """The groups whose name holds `part` (case-insensitive), the most mentioned first,
    then by name in the database's collation."""
    return [
        _group(record)
        for record in session.scalars(
            select(EntityGroupRecord)
            .where(EntityGroupRecord.name.ilike(f"%{part}%"))
            .order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.name)
            .limit(limit)
        )
    ]


def by_key(session: Session, ids: Collection[int]) -> list[int]:
    """The groups asked for, by key as the database orders it (its collation)."""
    if not ids:
        return []
    return list(
        session.scalars(
            select(EntityGroupRecord.id)
            .where(any_of(EntityGroupRecord.id, ids))
            .order_by(EntityGroupRecord.key)
        )
    )


@dataclass(frozen=True)
class SelectionCounts:
    """What steps 2–4 left, for the selection funnel."""

    # The articles whose latest successful extraction found a criminal-case event: the
    # very ones step 3 collects people from and the purge keeps.
    criminal_publications: int
    # Held for a person to look at, and not a case by the latest extraction.
    held: int
    groups: int
    figurants: int
    officials: int


_SELECTION = text(
    """
    WITH latest AS (
        SELECT DISTINCT ON (article_id) id, article_id FROM article_extraction_runs
        WHERE status = 'succeeded' ORDER BY article_id, id DESC
    ),
    criminal AS (
        SELECT DISTINCT l.article_id FROM latest l
        JOIN extracted_events e ON e.extraction_run_id = l.id
        WHERE e.event_type = ANY(:criminal)
    )
    SELECT (SELECT count(*) FROM criminal),
           (SELECT count(*) FROM junk_screen_holds h
            WHERE h.status = :held AND h.article_id NOT IN (SELECT article_id FROM criminal)),
           (SELECT count(*) FROM entity_groups),
           (SELECT count(*) FROM entity_group_roles WHERE role = 'figurant'),
           (SELECT count(*) FROM entity_group_roles WHERE kind = ANY(:officials))
    """
)


def selection_counts(
    session: Session, *, criminal: Collection[str], held: str, officials: Collection[str]
) -> SelectionCounts:
    return SelectionCounts(
        *session.execute(
            _SELECTION,
            {"criminal": list(criminal), "held": held, "officials": sorted(officials)},
        ).one()
    )

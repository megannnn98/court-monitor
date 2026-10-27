"""«Расследование»: the dossier of one person — who, which case, what happened when, on
what evidence, why political, whether on the Rosfinmonitoring list, who and what around.

Nothing here decides: it reads what the steps wrote (the entity, its role, its verdict,
its Rosfinmonitoring matches, the events it is the target of, the charges) and shows
each conclusion beside its evidence. A fixed number of queries per page, whatever the
person's size: the timeline and the publications are capped. The graph of events is
not built here: the page's script reads it from `web.investigation_graph`.

The dates: an extracted event carries the date of its publication (or none, when its
text names another year); no event date is invented, and the page says so.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.orm import Session

from analysis import read as verdicts
from analysis.operator_base.known_base import KnownBase, KnownMatch
from analysis.politics.classification import POLITICAL
from analysis.rosfinmonitoring.rf_check import FULL
from analysis.rosfinmonitoring.rf_entry import source_mark
from application import group_followers
from ingestion import read as articles
from processing import read as identity
from processing.evidence.publications import Evidence, latest_first, person_evidence, quoted
from processing.identity.service import IdentityDownstream
from web.views.workload import dispute_pairs

TIMELINE_LIMIT = 200

PUBLICATION_LIMIT = 30

RELATED_LIMIT = 30

QUOTE_CONTEXT = 160

VERDICT_METHODS = {
    "article": "правило: статья УК из списка политических (или только обычной уголовщины)",
    "memorial": "правило: категория реестра «Мемориала»",
    "base": "база оператора: её запись с этим именем и той же статьёй УК",
    "model": "модель по цитатам из публикаций",
    "manual": "решено оператором вручную",
}

ORG_ROLES = {"court": "суд", "authority": "орган"}


@dataclass(frozen=True)
class Entity:
    """The group with its role, its verdict and the news of its case, as the header
    shows them."""

    id: int
    key: str
    name: str
    variants: tuple[tuple[Any, ...], ...]
    regions: tuple[tuple[Any, ...], ...]
    mention_count: int
    article_count: int
    last_published_at: datetime | None
    name_source: str
    gender: str | None
    event_types: Any
    role: str | None
    kind: str | None
    role_method: str | None
    role_reason: str | None
    role_quote: str | None
    verdict: str | None
    verdict_method: str | None
    verdict_reason: str | None
    verdict_quote: str | None
    news_kind: str | None
    news_reason: str | None
    first_published_at: datetime | None


@dataclass(frozen=True)
class _EventRow:
    id: int
    event_type: str
    event_date: datetime | None
    confidence: float | None
    extractor_name: str
    start_offset: int
    end_offset: int
    article_id: int
    title: str
    published_at: datetime | None
    canonical_url: str
    source: str
    quote: str | None
    quote_start: int


@dataclass
class Source:
    article_id: int
    title: str
    source: str
    url: str
    published_at: datetime | None
    quote: str
    # The marked span: inside the quote, and in the whole text (for the link).
    start: int
    end: int
    text_start: int
    text_end: int


@dataclass
class TimelineItem:
    """One event of the case: the events of one type on one day, merged —
    several publications telling the same event are one item with several sources.

    An item is keyed by the event's own day where the text gave one, and by the day its
    publication came out otherwise. Those two are different facts and the page says which
    one it is showing (`dated`)."""

    event_type: str
    # The day this item is placed on, and `dated` says whose day it is.
    day: datetime | None
    dated: bool
    confidence: float | None
    extractor: str
    sources: list[Source] = field(default_factory=list)
    articles: set[str] = field(default_factory=set)
    orgs: set[tuple[str, str]] = field(default_factory=set)


@dataclass
class Publication:
    article_id: int
    title: str
    source: str
    url: str
    published_at: datetime | None
    quote: str
    start: int
    end: int
    text_start: int
    text_end: int
    evidence_kind: str = "mention"
    resolution: str | None = None
    rf_name: str | None = None
    rf_birth_date: date | None = None
    decided_at: datetime | None = None
    events: set[str] = field(default_factory=set)
    articles: set[str] = field(default_factory=set)
    others: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Dossier:
    entity: Any
    rf: list[Any]
    snapshot_date: datetime | None
    timeline: list[TimelineItem]
    events_capped: bool
    charges: dict[str, dict[str, Any]]
    publications: list[Publication]
    related: list[tuple[str, str, int]]
    verdict_source: int | None
    disputes: list[str]
    # What the operator's base says of the person; `known_loaded` False: no base to ask.
    known: KnownMatch | None = None
    known_loaded: bool = False


def _entity(db: Session, key: str, followers: IdentityDownstream) -> Entity | None:
    group = identity.groups_by_key(db, [key]).get(key)
    if group is None:
        return None
    role = identity.roles(db, [group.id]).get(group.id)
    politics = verdicts.politics(db, [group.id]).get(group.id)
    news = verdicts.case_news(db, [group.id]).get(group.id)
    published = [
        item.published_at
        for item in person_evidence(db, followers, groups=[group.id], quotes=False)
        if item.published_at is not None
    ]
    return Entity(
        id=group.id,
        key=group.key,
        name=group.name,
        variants=group.variants,
        regions=group.regions,
        mention_count=group.mention_count,
        article_count=group.article_count,
        last_published_at=group.last_published_at,
        name_source=group.name_source,
        gender=group.gender,
        event_types=group.event_types,
        role=role.role if role else None,
        kind=role.kind if role else None,
        role_method=role.method if role else None,
        role_reason=role.reason if role else None,
        role_quote=role.quote if role else None,
        verdict=politics.verdict if politics else None,
        verdict_method=politics.method if politics else None,
        verdict_reason=politics.reason if politics else None,
        verdict_quote=politics.quote if politics else None,
        news_kind=news.kind if news else None,
        news_reason=news.reason if news else None,
        first_published_at=min(published) if published else None,
    )


def _event_rows(db: Session, group_id: int, *, limit: int) -> list[_EventRow]:
    """The events whose target this person is, the earliest publication first (unknown
    dates last), with an excerpt around the trigger."""
    events = identity.target_events(db, group_id)
    cards = articles.article_cards(db, {event.article_id for event in events})
    chosen = sorted(
        (event for event in events if event.article_id in cards),
        key=lambda event: (
            cards[event.article_id].published_at is None,
            cards[event.article_id].published_at or datetime.min.replace(tzinfo=UTC),
            event.id,
        ),
    )[:limit]
    starts = [max(event.start_offset - QUOTE_CONTEXT, 0) for event in chosen]
    quotes = articles.text_windows(
        db,
        [
            articles.TextWindow(event.article_id, start, event.end_offset - start + QUOTE_CONTEXT)
            for event, start in zip(chosen, starts, strict=True)
        ],
    )
    return [
        _EventRow(
            id=event.id,
            event_type=event.event_type,
            event_date=event.event_date,
            confidence=event.confidence,
            extractor_name=event.extractor_name,
            start_offset=event.start_offset,
            end_offset=event.end_offset,
            article_id=event.article_id,
            title=cards[event.article_id].title,
            published_at=cards[event.article_id].published_at,
            canonical_url=cards[event.article_id].url,
            source=cards[event.article_id].source_name,
            quote=quote,
            quote_start=start,
        )
        for event, start, quote in zip(chosen, starts, quotes, strict=True)
    ]


def _first_per_article(evidence: list[Evidence]) -> list[Evidence]:
    """Each publication once, by its first place: a named mention before an unnamed
    figurant, then the earliest in the text."""
    first: dict[int, Evidence] = {}
    for item in sorted(evidence, key=lambda item: (item.kind, item.start_offset)):
        first.setdefault(item.article_id, item)
    return list(first.values())


def load(db: Session, key: str) -> Dossier | None:
    followers = group_followers()
    entity = _entity(db, key, followers)
    if entity is None:
        return None
    matches, snapshot_date = verdicts.list_matches(db, entity.id)

    event_rows = _event_rows(db, entity.id, limit=TIMELINE_LIMIT + 1)
    events_capped = len(event_rows) > TIMELINE_LIMIT
    event_rows = event_rows[:TIMELINE_LIMIT]
    charges: dict[str, dict[str, Any]] = {}
    articles_by_event: dict[int, set[str]] = defaultdict(set)
    articles_by_publication: dict[int, set[str]] = defaultdict(set)
    for found in identity.charges(db, [entity.id]).get(entity.id, []):
        charge = charges.setdefault(
            found.article, {"parts": set(), "sole": False, "publications": set()}
        )
        if found.part:
            charge["parts"].add(found.part)
        charge["sole"] = charge["sole"] or found.other_targets == 0
        charge["publications"].add(found.publication_id)
        articles_by_event[found.event_id].add(found.article)
        articles_by_publication[found.publication_id].add(found.article)
    orgs_by_event: dict[int, set[tuple[str, str]]] = defaultdict(set)
    for event_id, role, name in identity.event_orgs(db, [row.id for row in event_rows]):
        if name:
            orgs_by_event[event_id].add((role, " ".join(str(name).split())))

    items: dict[tuple[str, Any], TimelineItem] = {}
    for row in event_rows:
        # Two days, kept apart: the event's own, and the publication's. They are merged only
        # for placement, and `dated` records which one the item is showing.
        own = row.event_date
        moment = own or row.published_at
        # The day as the page shows it: the local one.
        day = moment.astimezone().date() if moment else None
        item = items.setdefault(
            (row.event_type, day),
            TimelineItem(
                event_type=row.event_type,
                day=own or row.published_at,
                dated=own is not None,
                confidence=row.confidence,
                extractor=row.extractor_name,
            ),
        )
        item.dated = item.dated and row.event_date is not None
        item.articles |= articles_by_event.get(row.id, set())
        item.orgs |= orgs_by_event.get(row.id, set())
        if all(source.article_id != row.article_id for source in item.sources):
            item.sources.append(
                Source(
                    row.article_id,
                    row.title,
                    row.source,
                    row.canonical_url,
                    row.published_at,
                    row.quote or "",
                    row.start_offset - row.quote_start,
                    row.end_offset - row.quote_start,
                    row.start_offset,
                    row.end_offset,
                )
            )
    evidence = person_evidence(
        db, followers, groups=[entity.id], context=QUOTE_CONTEXT, quotes=False
    )
    latest = sorted(
        _first_per_article(evidence),
        key=lambda item: (*latest_first(item.published_at), -item.article_id),
    )[:PUBLICATION_LIMIT]
    publications = [
        Publication(
            item.article_id,
            item.title,
            item.source,
            item.url,
            item.published_at,
            item.quote or "",
            item.start_offset - item.quote_start,
            item.end_offset - item.quote_start,
            item.start_offset,
            item.end_offset,
            item.kind,
            item.resolution,
            item.rf_name,
            item.rf_birth_date,
            item.decided_at,
            articles=set(articles_by_publication.get(item.article_id, set())),
        )
        for item in quoted(db, latest, QUOTE_CONTEXT)
    ]
    events_by_publication: dict[int, set[str]] = defaultdict(set)
    for row in event_rows:
        events_by_publication[row.article_id].add(row.event_type)
    by_id = {publication.article_id: publication for publication in publications}
    for publication in publications:
        publication.events = events_by_publication.get(publication.article_id, set())
    if by_id:
        around = person_evidence(db, followers, articles=list(by_id), quotes=False)
        pairs = sorted(
            {(item.article_id, item.group_id) for item in around if item.group_id != entity.id}
        )
        names = identity.groups(db, {group_id for _, group_id in pairs})
        for article_id, group_id in pairs:
            # A rebuild committed between the two reads may have taken the group away.
            if (other := names.get(group_id)) is not None:
                by_id[article_id].others.append((other.key, other.name))
    related = related_people(db, entity.id, evidence, followers)

    verdict_source = None
    needle = " ".join((entity.verdict_quote or "").split())[:80]
    if needle:
        verdict_source = _quote_source(db, evidence, needle)
    disputes = [
        pair.right.key if pair.left.key == key else pair.left.key
        for pair in dispute_pairs(db)
        if key in pair.keys
    ]
    base = KnownBase.from_session(db)
    return Dossier(
        known=base.match(entity.name),
        known_loaded=bool(len(base)),
        entity=entity,
        rf=list(matches),
        snapshot_date=snapshot_date,
        timeline=sorted(
            items.values(),
            key=lambda item: (
                item.day is None,
                item.day or datetime.min.replace(tzinfo=UTC),
                item.event_type,
            ),
        ),
        events_capped=events_capped,
        charges=charges,
        publications=publications,
        related=related,
        verdict_source=verdict_source,
        disputes=disputes,
    )


def related_people(
    db: Session,
    group_id: int,
    evidence: list[Evidence],
    followers: IdentityDownstream,
    *,
    limit: int = RELATED_LIMIT,
) -> list[tuple[str, str, int]]:
    """The people most often in the same publications as the group's `evidence`, the
    most shared first, then by name: a count, not a connection."""
    mine = {item.article_id for item in evidence}
    shared: dict[int, set[int]] = defaultdict(set)
    for item in person_evidence(db, followers, articles=mine, quotes=False):
        if item.group_id != group_id:
            shared[item.group_id].add(item.article_id)
    # Each read is a statement of its own: a group a rebuild took away between them is
    # left out, not looked up.
    order = {other: rank for rank, other in enumerate(identity.by_name(db, shared))}
    top = sorted(
        (other for other in shared if other in order),
        key=lambda other: (-len(shared[other]), order[other]),
    )[:limit]
    names = identity.groups(db, top)
    return [
        (names[other].key, names[other].name, len(shared[other])) for other in top if other in names
    ]


def _quote_source(db: Session, evidence: list[Evidence], needle: str) -> int | None:
    """The publication a verdict's quote comes from (its text, spaces folded)."""
    in_text = articles.texts_containing(
        db, {item.article_id for item in evidence if item.kind == "mention"}, needle
    )
    found = [
        item
        for item in evidence
        if (item.kind == "mention" and item.article_id in in_text)
        or (item.kind != "mention" and needle in re.sub(r"\s+", " ", item.quote or ""))
    ]
    if not found:
        return None
    return min(found, key=lambda item: latest_first(item.published_at)).article_id


def _day(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%d.%m.%Y") if moment else "—"


def rf_status(matches: list[Any]) -> tuple[str, str]:
    """(label, badge): on the list for certain, maybe a namesake, or not found."""
    if any(match.level == FULL for match in matches):
        return "в перечне Росфинмониторинга", ""
    if matches:
        return "возможно в перечне (тёзка без отчества)", "pending"
    return "не найден в перечне", ""


def rf_entry_text(row: Any) -> str:
    """One matched entry of the list as text. The day of inclusion is said about the entry,
    and only where it was matched with the patronymic: on a namesake the day is somebody
    else's."""
    return (
        f"{row.full_name}{f', {row.birth_date:%d.%m.%Y} г.р.' if row.birth_date else ''}"
        f"{f', {row.birth_place}' if row.birth_place else ''}"
        f"{f' — запись перечня включена {row.inclusion_date:%d.%m.%Y}{source_mark(row.inclusion_source)}' if row.level == FULL and row.inclusion_date else ''}"
    )


def warnings(dossier: Dossier) -> list[str]:
    """What the dossier cannot vouch for; read by the legacy page and the v1 route."""
    entity = dossier.entity
    found = ["Даты событий — даты публикаций: точной даты события в базе нет."]
    if any(not charge["sole"] for charge in dossier.charges.values()):
        found.append("Часть статей УК «общая»: в событии обвиняемыми названы и другие люди.")
    if dossier.rf and not any(row.level == FULL for row in dossier.rf):
        found.append("Совпадение с перечнем только по имени и фамилии: может быть тёзка.")
    if entity.verdict == "unclear" or entity.role == "unclear":
        found.append("Система не смогла решить — случай в «Очереди».")
    if dossier.disputes:
        found.append("Есть нерешённые спорные пары: возможно, это тот же человек.")
    if entity.name_source == "rules":
        found.append("Имя собрано правилами, не проверено моделью или человеком.")
    return found


def identification(publication: Publication) -> str | None:
    """For a publication that names nobody, how the operator identified the person."""
    if publication.evidence_kind != "unnamed_resolution":
        return None
    source = (
        f"запись РФМ {publication.rf_name}, {publication.rf_birth_date:%d.%m.%Y}"
        if publication.resolution == "rf_entry"
        and publication.rf_name
        and publication.rf_birth_date
        else "существующий человек"
        if publication.resolution == "existing_person"
        else "имя указано вручную"
        if publication.resolution == "supplied_name"
        else "ручное решение"
    )
    return (
        f"Опознан оператором: {source}"
        f"{f', {_day(publication.decided_at)}' if publication.decided_at else ''}. "
        "Цитата оставлена как в публикации."
    )


SEARCH_LIMIT = 50


def search(db: Session, q: str) -> tuple[list[identity.Group], dict[int, tuple[str, str | None]]]:
    """By any form of the name, the most mentioned first; with no name, the latest
    political cases. With each found person's role. Read by the legacy page and the v1
    route."""
    if q.strip():
        ids = identity.filtered_group_ids(db, q=q, article="", region="")
        order = "mentions"
    else:
        ids = verdicts.groups_with_verdict(db, POLITICAL)
        order = "recent"
    found = identity.ordered_groups(db, ids, order=order, offset=0, limit=SEARCH_LIMIT)
    roles = {
        group_id: (role.role, role.kind)
        for group_id, role in identity.roles(db, [entity.id for entity in found]).items()
    }
    return found, roles

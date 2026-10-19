"""The graph of an investigation: what the events state, as nodes and edges.

A read model over PostgreSQL, built for the dossier's interactive graph. It does not
decide and does not store: every edge is a row the pipeline wrote.

The centre of everything is the event. A person is tied to an event the extraction named
them in; the event is tied to the publications that tell it (each with the span of its
text), to the court and the body it names, and to the articles of the Code it rests on. Two people are
connected through an event both are named in — never directly. That two people stand in
the same publications is a count, not a fact about them: it is a separate kind of edge
(`cooccurrence`, `source: derived`), and the page hides it until asked.

One arrest told by seven publications is seven rows of `extracted_events`, one in each
publication. The graph draws them as one event: the reports of one type, dated one day,
that name a common person, are one node (`_clusters`). This is a reading of the data, not
a row of it — the node says how many reports it holds, and each stays one click away. A
person only mentioned in the case (a judge, a lawyer, an official) binds nothing: one
judge passing thirty sentences in a day is thirty events.

The graph is never read whole. The first answer is the person and their latest events;
an event or a person is expanded on request, each by a fixed number of queries whatever
its size, and what a limit cut is counted in `more`.

People are the entities of step 3 (`entity_groups`); `persons` is not read. A person's
node is named by the entity's key, not its id: step 3 rebuilds the groups and hands the
ids out anew, and a page left open must not open somebody else's events under a name
that is already drawn.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import quote, urlencode

from sqlalchemy.orm import Session

from application import group_followers
from ingestion import read as articles
from processing import read as identity
from processing.evidence.publications import person_evidence
from web.ui.dossier import related_people
from web.ui.entities import EVENT_LABELS, display_name

INITIAL_EVENTS = 20
PERSON_EVENTS = 20
EVENT_PEOPLE = 20
EVENT_NEIGHBOURS = 20
EVENT_PUBLICATIONS = 10
COOCCURRENCE_PEOPLE = 10
# An event's id as the database has it: ASCII digits, within an integer of four bytes.
# `str.isdigit` would let «²» through, and `int` would then fail on it.
_EVENT_ID = re.compile(r"[0-9]{1,9}")

PERSON = "person"
EVENT = "event"
PUBLICATION = "publication"
COURT = "court"
AUTHORITY = "authority"
CRIMINAL_ARTICLE = "criminal_article"
ADMINISTRATIVE_ARTICLE = "administrative_article"

# Where an edge comes from. `manual` is an operator's hypothesis: nothing writes it yet,
# and when something does it must never look like the other two.
EXTRACTED = "extracted"
DERIVED = "derived"
MANUAL = "manual"

COOCCURRENCE = "cooccurrence"
EVIDENCE = "evidence"

# The roles of `event_entity_mentions`, as the edge says them. A person's only role is
# `target`, and the extraction gives it to everyone the sentence names — a lawyer and a
# judge too — so the edge says «назван в событии», not «фигурант».
_ROLE_LABELS = {
    "target": "назван в событии",
    "court": "суд",
    "authority": "орган",
    "legal_basis": "статья",
}
_ORG_TYPES = {"court": COURT, "authority": AUTHORITY}


class UnknownNode(LookupError):
    """No such person or event."""


class NotExpandable(ValueError):
    """A node id that is no person and no event, or is not an id at all."""


@dataclass
class Graph:
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: dict[str, dict[str, Any]] = field(default_factory=dict)
    # What the limits cut: «+ 37 событий» beside the node they belong to.
    more: list[dict[str, Any]] = field(default_factory=list)
    center: str | None = None

    def node(self, node_id: str, node_type: str, label: str, **fields: Any) -> str:
        self.nodes.setdefault(node_id, {"id": node_id, "type": node_type, "label": label, **fields})
        return node_id

    def edge(
        self,
        start: str,
        end: str,
        edge_type: str,
        label: str,
        *,
        source: str = EXTRACTED,
        **fields: Any,
    ) -> None:
        edge_id = f"{start}>{end}:{edge_type}"
        self.edges.setdefault(
            edge_id,
            {
                "id": edge_id,
                "from": start,
                "to": end,
                "type": edge_type,
                "label": label,
                "source": source,
                **fields,
            },
        )

    def cut(self, node_id: str, node_type: str, count: int) -> None:
        if count > 0:
            self.more.append({"node": node_id, "type": node_type, "count": count})

    def payload(self) -> dict[str, Any]:
        answer: dict[str, Any] = {
            "nodes": list(self.nodes.values()),
            "edges": list(self.edges.values()),
            "more": self.more,
        }
        if self.center is not None:
            answer["center"] = self.center
        return answer


@dataclass(frozen=True)
class _ReportRow:
    """An event as one publication tells it, with one person it names (or none)."""

    id: int
    event_type: str
    event_date: datetime | None
    confidence: float
    start_offset: int
    end_offset: int
    article_id: int
    title: str
    source: str
    published_day: date | None
    # The day this report is placed on, and `dated` says whose day it is: the event's own
    # where the text gave one (`dated`), otherwise the publication's. The two are never
    # mixed in one key, so a publication date cannot make one event look like two.
    day: date | None
    dated: bool
    group_id: int | None
    role: str | None
    binds: bool | None


def _report_rows(db: Session, seeds: Collection[int]) -> list[_ReportRow]:
    """The reports that may be one event with the seeds': the seeds' own, and every
    report of the same type dated the same day, each with the people it names, in id
    order. `binds`: the person is more than mentioned in their case, so two reports
    naming them are of one event."""
    facts = identity.event_facts(db, seeds)
    days = articles.published_days(db, {fact.article_id for fact in facts.values()})
    keys = {
        (fact.event_type, day)
        for fact in facts.values()
        if fact.article_id in days and (day := fact.event_day or days[fact.article_id]) is not None
    }
    published_then = articles.published_on(db, {day for _, day in keys})
    found = set(facts) | set(identity.events_dated(db, keys))
    found |= set(
        identity.events_undated(db, published_then, {event_type for event_type, _ in keys})
    )
    facts = identity.event_facts(db, found)
    cards = articles.article_cards(db, {fact.article_id for fact in facts.values()})
    days = articles.published_days(db, cards)
    reports = {
        event_id: fact
        for event_id, fact in facts.items()
        if fact.article_id in cards
        and (
            event_id in seeds or (fact.event_type, fact.event_day or days[fact.article_id]) in keys
        )
    }
    return [
        _ReportRow(
            id=fact.id,
            event_type=fact.event_type,
            event_date=fact.event_date,
            confidence=fact.confidence,
            start_offset=fact.start_offset,
            end_offset=fact.end_offset,
            article_id=fact.article_id,
            title=cards[fact.article_id].title,
            source=cards[fact.article_id].source_name,
            published_day=days[fact.article_id],
            day=fact.event_day or days[fact.article_id],
            dated=fact.event_day is not None,
            group_id=person.group_id,
            role=person.role,
            binds=person.binds,
        )
        for person in sorted(identity.event_people(db, reports), key=lambda row: row.event_id)
        if (fact := reports[person.event_id])
    ]


@dataclass(frozen=True)
class _Named:
    """A person as a node shows them."""

    key: str
    name: str


@dataclass
class _Report:
    """One row of `extracted_events`: an event as one publication tells it."""

    row: Any
    # The people it names, by group: the role the extraction gave each.
    people: dict[int, str] = field(default_factory=dict)
    binders: set[int] = field(default_factory=set)


@dataclass
class _Event:
    """The reports drawn as one event; the first is the one the node's id is made of."""

    reports: list[_Report]

    @property
    def root(self) -> Any:
        return self.reports[0].row

    @property
    def day(self) -> date | None:
        day: date | None = self.root.day
        return day

    def role_of(self, group: int) -> str | None:
        roles = [report.people[group] for report in self.reports if group in report.people]
        return min(roles) if roles else None


def _reports(rows: Any) -> dict[int, _Report]:
    found: dict[int, _Report] = {}
    for row in rows:
        report = found.setdefault(row.id, _Report(row))
        if row.group_id is not None:
            # One person named twice in the sentence: the first role by name, as SQL's min.
            report.people[row.group_id] = min(row.role, report.people.get(row.group_id, row.role))
            if row.binds:
                report.binders.add(row.group_id)
    return found


def _clusters(reports: dict[int, _Report]) -> list[_Event]:
    """The reports as events: those of one type, one day and one person (through any chain
    of such reports) are one event.

    A report of a known event date is placed by that date, so two sources publishing one
    event on different days are one event: the date both write is the same. A report whose
    date the text did not give is placed by its publication day instead — the best available,
    and it merges with a report that writes that very day. A report with no day stands alone."""
    parent = {report_id: report_id for report_id in reports}

    def find(report_id: int) -> int:
        while parent[report_id] != report_id:
            parent[report_id] = parent[parent[report_id]]
            report_id = parent[report_id]
        return report_id

    first: dict[tuple[str, date, int], int] = {}
    for report_id, report in reports.items():
        if report.row.day is None:
            continue
        for group in report.binders:
            other = first.setdefault((report.row.event_type, report.row.day, group), report_id)
            parent[find(report_id)] = find(other)
    # In the order of the ids: an event is named by its first report, and keeps the name
    # as later reports join it.
    members: dict[int, list[_Report]] = defaultdict(list)
    for report_id in sorted(reports):
        members[find(report_id)].append(reports[report_id])
    return [_Event(found) for found in members.values()]


def _person_node(graph: Graph, row: Any) -> str:
    return graph.node(
        f"{PERSON}:{row.key}",
        PERSON,
        display_name(row.name),
        href=f"/ui/investigations/{quote(row.key, safe='')}",
        expandable=True,
    )


def _span_href(row: Any) -> str:
    return f"/ui/articles/{row.article_id}?{urlencode({'start': row.start_offset, 'end': row.end_offset})}"


def _event_node(graph: Graph, event: _Event) -> str:
    """The event, with its provenance: how many reports it is drawn from, their sources,
    and the span of the first in its publication. `date` is the event's own where the texts
    gave one (`dated`), otherwise the publication's."""
    root = event.root
    return graph.node(
        f"{EVENT}:{root.id}",
        EVENT,
        EVENT_LABELS.get(root.event_type, root.event_type),
        event_type=root.event_type,
        date=event.day.isoformat() if event.day else None,
        dated=any(report.row.dated for report in event.reports),
        confidence=max(report.row.confidence for report in event.reports),
        reports=len(event.reports),
        publications=len({report.row.article_id for report in event.reports}),
        sources=sorted({report.row.source for report in event.reports}),
        href=_span_href(root),
        expandable=True,
    )


def _events_of(db: Session, graph: Graph, person_id: str, group: int, limit: int) -> None:
    """The events a person is named in, the latest first; what the limit cut is counted."""
    events = [
        event
        for event in _clusters(_reports(_report_rows(db, identity.events_naming(db, group))))
        if event.role_of(group) is not None
    ]
    events.sort(key=lambda event: (event.day or date.min, event.root.id), reverse=True)
    for event in events[:limit]:
        role = event.role_of(group) or ""
        graph.edge(person_id, _event_node(graph, event), role, _ROLE_LABELS.get(role, role))
    graph.cut(person_id, EVENT, len(events) - limit)


def _person(db: Session, key: str) -> Any:
    row = identity.groups_by_key(db, [key]).get(key)
    if row is None:
        raise UnknownNode(f"no person: {key}")
    return row


def known_person(db: Session, key: str) -> None:
    """Raises `UnknownNode` for a key that names nobody."""
    _person(db, key)


def initial_graph(db: Session, key: str) -> dict[str, Any]:
    """The person, their latest events, and the people of the same publications (as
    `cooccurrence` edges only). Three queries."""
    person = _person(db, key)
    graph = Graph()
    graph.center = _person_node(graph, person)
    _events_of(db, graph, graph.center, person.id, INITIAL_EVENTS)
    followers = group_followers()
    evidence = person_evidence(db, followers, groups=[person.id], quotes=False)
    for other_key, other_name, shared in related_people(
        db, person.id, evidence, followers, limit=COOCCURRENCE_PEOPLE
    ):
        graph.edge(
            graph.center,
            _person_node(graph, _Named(other_key, other_name)),
            COOCCURRENCE,
            f"общих публикаций: {shared}",
            source=DERIVED,
            shared_publications=shared,
        )
    return graph.payload()


def _article_node(graph: Graph, data: dict[str, Any], surface: str) -> tuple[str, str] | None:
    """The article of the Code a legal reference names, and how the edge says its part.
    None for a reference that names no article or no Code: which Code it is would be a
    guess."""
    article = str(data.get("article") or "").strip()
    code = " ".join(str(data.get("code") or "").split())
    if not article or not code:
        return None
    # «УК» and «УК РФ» are one Code (step 3 reads them so): one article, one node. The
    # label keeps the Code as the text wrote it.
    head = re.sub(r"\s*РФ$", "", code, flags=re.IGNORECASE)
    criminal = head.upper() == "УК"
    node = graph.node(
        f"article:{head}:{article}",
        CRIMINAL_ARTICLE if criminal else ADMINISTRATIVE_ARTICLE,
        f"ст. {article} {code}",
        href=f"/ui/entities?{urlencode({'article': article, 'figurants': 'all', 'rf': 'all'})}"
        if criminal
        else None,
        expandable=False,
    )
    part = ", ".join(
        f"{name} {data[field_]}"
        for field_, name in (("part", "ч."), ("clause", "п."))
        if data.get(field_)
    )
    return node, " ".join(surface.split()) if not part else part


def expand_event(db: Session, event_id: int) -> dict[str, Any]:
    """What the event names: the people (those resolved to a person), the publications
    that tell it, the court, the body, the articles. Two queries."""
    reports = _reports(_report_rows(db, [event_id]))
    if event_id not in reports:
        raise UnknownNode(f"no event: {event_id}")
    event = next(
        found for found in _clusters(reports) if any(r.row.id == event_id for r in found.reports)
    )
    graph = Graph()
    node_id = _event_node(graph, event)
    # Each publication once, by the first report in it; the edge leads to that report's
    # very words.
    told: dict[int, Any] = {}
    for report in event.reports:
        told.setdefault(report.row.article_id, report.row)
    for row in list(told.values())[:EVENT_PUBLICATIONS]:
        publication = graph.node(
            f"{PUBLICATION}:{row.article_id}",
            PUBLICATION,
            row.title,
            publication_source=row.source,
            date=row.published_day.isoformat() if row.published_day else None,
            href=f"/ui/articles/{row.article_id}",
            expandable=False,
        )
        graph.edge(node_id, publication, EVIDENCE, "источник", href=_span_href(row))
    graph.cut(node_id, PUBLICATION, len(told) - EVENT_PUBLICATIONS)

    people: dict[int, Any] = {}
    unresolved: set[str] = set()
    # A court named twice in the event is one court: by its node, in the order of the text.
    orgs: dict[str, tuple[str, str]] = {}
    mentions = identity.event_mentions(db, [report.row.id for report in event.reports])
    for mention in mentions:
        if mention.entity_type == "person":
            if mention.group_id is None:
                # One name written twice in the sentence is one name.
                unresolved.add(" ".join((mention.normalized_text or "").split()).casefold())
            else:
                people.setdefault(mention.group_id, mention)
            continue
        if mention.role in _ORG_TYPES:
            name = " ".join((mention.surface_text or "").split())
            if name:
                folded = " ".join((mention.normalized_text or name).split()).casefold()
                orgs.setdefault(f"org:{mention.role}:{folded}", (mention.role, name))
        elif mention.role == "legal_basis":
            found = _article_node(
                graph, dict(mention.normalized_data or {}), mention.surface_text or ""
            )
            if found is not None:
                graph.edge(
                    node_id, found[0], "legal_basis", _ROLE_LABELS["legal_basis"], title=found[1]
                )
    for org_id, (role, name) in list(orgs.items())[:EVENT_NEIGHBOURS]:
        graph.node(
            org_id,
            _ORG_TYPES[role],
            name,
            href=f"/ui/publications?{urlencode({'q': name})}",
            expandable=False,
        )
        graph.edge(node_id, org_id, role, _ROLE_LABELS[role])
    for person in list(people.values())[:EVENT_PEOPLE]:
        graph.edge(
            _person_node(graph, person),
            node_id,
            person.role,
            _ROLE_LABELS.get(person.role, person.role),
        )
    graph.cut(node_id, PERSON, len(people) - EVENT_PEOPLE)
    # Names the event gives that step 3 made no person of: counted, not drawn — a name
    # in a text is not an identity.
    graph.cut(node_id, "unresolved_person", len(unresolved))
    graph.cut(node_id, "organization", len(orgs) - EVENT_NEIGHBOURS)
    return graph.payload()


def expand_person(db: Session, key: str) -> dict[str, Any]:
    """The events a person is named in. Two queries."""
    row = _person(db, key)
    graph = Graph()
    _events_of(db, graph, _person_node(graph, row), row.id, PERSON_EVENTS)
    return graph.payload()


def expand(db: Session, node: str) -> dict[str, Any]:
    """The neighbours of `person:<key>` or `event:<id>`."""
    kind, _, name = node.partition(":")
    if kind == PERSON and name:
        return expand_person(db, name)
    if kind == EVENT and _EVENT_ID.fullmatch(name):
        return expand_event(db, int(name))
    raise NotExpandable(node)

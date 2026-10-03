"""The graph of an investigation: what the events state, as nodes and edges.

A read model over PostgreSQL, built for the dossier's interactive graph. It does not
decide and does not store: every edge is a row the pipeline wrote.

The centre of everything is the event. A person is tied to an event the extraction named
them in; the event is tied to its one publication (with the span of its text), to the
court and the body it names, and to the articles of the Code it rests on. Two people are
connected through an event both are named in — never directly. That two people stand in
the same publications is a count, not a fact about them: it is a separate kind of edge
(`cooccurrence`, `source: derived`), and the page hides it until asked.

The graph is never read whole. The first answer is the person and their latest events;
an event or a person is expanded on request, each by a fixed number of queries whatever
its size, and what a limit cut is counted in `more`.

People are the entities of step 3 (`entity_groups`); `persons` is not read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

from sqlalchemy import text
from sqlalchemy.orm import Session

from entities.evidence import person_evidence_cte
from web.ui.entities import _EVENT_LABELS, display_name

INITIAL_EVENTS = 20
PERSON_EVENTS = 20
EVENT_PEOPLE = 20
EVENT_NEIGHBOURS = 20
COOCCURRENCE_PEOPLE = 10

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


_EVENT_COLUMNS = """
    e.id, e.event_type, e.event_date, e.confidence, e.start_offset, e.end_offset,
    a.id AS article_id, a.title, a.published_at, s.name AS source
"""
_EVENT_JOINS = """
    FROM extracted_events e
    JOIN article_extraction_runs r ON r.id = e.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
"""
_PERSON = "SELECT id, key, name FROM entity_groups WHERE {column} = :value"
# The events a person is named in, the latest first, with how many there are in all.
_PERSON_EVENTS = text(
    f"""
    SELECT {_EVENT_COLUMNS}, count(*) OVER () AS total
    {_EVENT_JOINS}
    WHERE e.id IN (
        SELECT em.event_id
        FROM entity_group_mentions gm
        JOIN event_entity_mentions em ON em.mention_id = gm.mention_id AND em.role = 'target'
        WHERE gm.group_id = :group
    )
    ORDER BY coalesce(e.event_date, a.published_at) DESC NULLS LAST, e.id DESC
    LIMIT :limit
    """
)
_EVENT = text(f"SELECT {_EVENT_COLUMNS} {_EVENT_JOINS} WHERE e.id = :event")
# Everything an event names, with the person a name was resolved to (if it was).
_EVENT_MENTIONS = text(
    """
    SELECT em.role, m.entity_type, m.surface_text, m.normalized_text, m.normalized_data,
           g.id AS group_id, g.key, g.name
    FROM event_entity_mentions em
    JOIN entity_mentions m ON m.id = em.mention_id
    LEFT JOIN entity_group_mentions gm ON gm.mention_id = m.id
    LEFT JOIN entity_groups g ON g.id = gm.group_id
    WHERE em.event_id = :event
    ORDER BY em.role, m.start_offset, m.id
    """
)
# The people most often in the same publications: a count, not a connection.
_COOCCURRENCE = text(
    f"""
    WITH {person_evidence_cte()},
    mine AS (SELECT DISTINCT article_id FROM person_evidence WHERE group_id = :group)
    SELECT g.id, g.key, g.name, count(DISTINCT r.article_id) AS shared
    FROM person_evidence r
    JOIN entity_groups g ON g.id = r.group_id
    WHERE r.article_id IN (SELECT article_id FROM mine) AND r.group_id <> :group
    GROUP BY g.id, g.key, g.name ORDER BY shared DESC, g.name LIMIT :limit
    """
)


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone().date().isoformat() if moment else None


def _person_node(graph: Graph, row: Any) -> str:
    return graph.node(
        f"{PERSON}:{row.id}",
        PERSON,
        display_name(row.name),
        href=f"/ui/investigations/{quote(row.key)}",
        expandable=True,
    )


def _event_node(graph: Graph, row: Any) -> str:
    """The event, with its provenance: the publication it was read in and the span of
    its text. Its date is the publication's unless the text gave one (`dated`)."""
    moment = row.event_date or row.published_at
    return graph.node(
        f"{EVENT}:{row.id}",
        EVENT,
        _EVENT_LABELS.get(row.event_type, row.event_type),
        event_type=row.event_type,
        date=_iso(moment),
        dated=row.event_date is not None,
        confidence=row.confidence,
        publication=f"{PUBLICATION}:{row.article_id}",
        publication_title=row.title,
        publication_source=row.source,
        href=_span_href(row),
        expandable=True,
    )


def _span_href(row: Any) -> str:
    return f"/ui/articles/{row.article_id}?{urlencode({'start': row.start_offset, 'end': row.end_offset})}"


def _events_of(db: Session, graph: Graph, person_id: str, group: int, limit: int) -> None:
    rows = db.execute(_PERSON_EVENTS, {"group": group, "limit": limit}).all()
    for row in rows:
        graph.edge(person_id, _event_node(graph, row), "target", _ROLE_LABELS["target"])
    if rows:
        graph.cut(person_id, EVENT, rows[0].total - len(rows))


def _person(db: Session, column: str, value: object) -> Any:
    row = db.execute(text(_PERSON.format(column=column)), {"value": value}).first()
    if row is None:
        raise UnknownNode(f"no person: {value}")
    return row


def known_person(db: Session, key: str) -> None:
    """Raises `UnknownNode` for a key that names nobody."""
    _person(db, "key", key)


def initial_graph(db: Session, key: str) -> dict[str, Any]:
    """The person, their latest events, and the people of the same publications (as
    `cooccurrence` edges only). Three queries."""
    person = _person(db, "key", key)
    graph = Graph()
    graph.center = _person_node(graph, person)
    _events_of(db, graph, graph.center, person.id, INITIAL_EVENTS)
    for row in db.execute(
        _COOCCURRENCE, {"group": person.id, "limit": COOCCURRENCE_PEOPLE, "context": 0}
    ).all():
        graph.edge(
            graph.center,
            _person_node(graph, row),
            COOCCURRENCE,
            f"общих публикаций: {row.shared}",
            source=DERIVED,
            shared_publications=row.shared,
        )
    return graph.payload()


def _article_node(graph: Graph, data: dict[str, Any], surface: str) -> tuple[str, str] | None:
    """The article of the Code a legal reference names, and how the edge says its part."""
    article = str(data.get("article") or "").strip()
    if not article:
        return None
    code = " ".join(str(data.get("code") or "").split())
    criminal = code.upper().startswith("УК")
    node = graph.node(
        f"article:{code or '?'}:{article}",
        CRIMINAL_ARTICLE if criminal else ADMINISTRATIVE_ARTICLE,
        " ".join(part for part in (f"ст. {article}", code) if part),
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


def expand_event(db: Session, event: int) -> dict[str, Any]:
    """What the event names: the people (those resolved to a person), its publication,
    the court, the body, the articles. Two queries."""
    row = db.execute(_EVENT, {"event": event}).first()
    if row is None:
        raise UnknownNode(f"no event: {event}")
    graph = Graph()
    event_id = _event_node(graph, row)
    publication = graph.node(
        f"{PUBLICATION}:{row.article_id}",
        PUBLICATION,
        row.title,
        publication_source=row.source,
        date=_iso(row.published_at),
        href=f"/ui/articles/{row.article_id}",
        expandable=False,
    )
    # The edge leads to the very words: the span of the event in the publication's text.
    graph.edge(event_id, publication, EVIDENCE, "источник", href=_span_href(row))

    people: dict[int, Any] = {}
    unresolved = 0
    neighbours = 0
    for mention in db.execute(_EVENT_MENTIONS, {"event": event}).all():
        if mention.entity_type == "person":
            if mention.group_id is None:
                unresolved += 1
            else:
                people.setdefault(mention.group_id, mention)
            continue
        if mention.role in _ORG_TYPES:
            name = " ".join((mention.surface_text or "").split())
            if not name:
                continue
            neighbours += 1
            if neighbours > EVENT_NEIGHBOURS:
                continue
            node = graph.node(
                f"org:{mention.role}:{' '.join((mention.normalized_text or name).split()).casefold()}",
                _ORG_TYPES[mention.role],
                name,
                href=f"/ui/publications?{urlencode({'q': name})}",
                expandable=False,
            )
            graph.edge(event_id, node, mention.role, _ROLE_LABELS[mention.role])
        elif mention.role == "legal_basis":
            found = _article_node(
                graph, dict(mention.normalized_data or {}), mention.surface_text or ""
            )
            if found is not None:
                graph.edge(
                    event_id, found[0], "legal_basis", _ROLE_LABELS["legal_basis"], title=found[1]
                )
    for person in list(people.values())[:EVENT_PEOPLE]:
        node = graph.node(
            f"{PERSON}:{person.group_id}",
            PERSON,
            display_name(person.name),
            href=f"/ui/investigations/{quote(person.key)}",
            expandable=True,
        )
        graph.edge(node, event_id, person.role, _ROLE_LABELS.get(person.role, person.role))
    graph.cut(event_id, PERSON, len(people) - EVENT_PEOPLE)
    # Names the event gives that step 3 made no person of: counted, not drawn — a name
    # in a text is not an identity.
    graph.cut(event_id, "unresolved_person", unresolved)
    return graph.payload()


def expand_person(db: Session, person: int) -> dict[str, Any]:
    """The events a person is named in. Two queries."""
    row = _person(db, "id", person)
    graph = Graph()
    _events_of(db, graph, _person_node(graph, row), row.id, PERSON_EVENTS)
    return graph.payload()


def expand(db: Session, node: str) -> dict[str, Any]:
    """The neighbours of `person:<id>` or `event:<id>`."""
    kind, _, number = node.partition(":")
    if kind not in (PERSON, EVENT) or not number.isdigit():
        raise NotExpandable(node)
    return expand_event(db, int(number)) if kind == EVENT else expand_person(db, int(number))

"""«Публикации»: the publications with a criminal case, searched by their title and text,
filtered by source; each with the people it names and the events found in it. A page is
three queries whatever its size: the rows, their people, their events."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from application import group_followers
from ingestion import read as articles
from processing import read as identity
from processing.evidence.publications import person_evidence

PAGE_SIZE = 50

PEOPLE_SHOWN = 8


def people_and_events(
    db: Session, article_ids: Sequence[int]
) -> tuple[dict[int, list[tuple[str, str, int]]], dict[int, list[tuple[str, int]]]]:
    """Per article, the people it names (key, name, mentions) and its events (type, count)."""
    people: dict[int, list[tuple[str, str, int]]] = defaultdict(list)
    events: dict[int, list[tuple[str, int]]] = defaultdict(list)
    if article_ids:
        pairs = sorted(
            {
                (item.article_id, item.group_id)
                for item in person_evidence(
                    db, group_followers(), articles=article_ids, quotes=False
                )
            }
        )
        groups = identity.groups(db, {group_id for _, group_id in pairs})
        for article_id, group_id in pairs:
            # A rebuild committed between the two reads may have taken the group away.
            if (group := groups.get(group_id)) is not None:
                people[article_id].append((group.key, group.name, group.mention_count))
        for article_id, event_type, count in identity.event_counts(db, article_ids):
            events[article_id].append((event_type, count))
    return people, events


@dataclass(frozen=True)
class PublicationPage:
    """One page of «Публикации»; read by the legacy page and `GET /api/v1/publications`."""

    total: int
    rows: Sequence[articles.ListedArticle]
    people: dict[int, list[tuple[str, str, int]]]
    events: dict[int, list[tuple[str, int]]]
    # Every source with its count of publications, for the filter.
    sources: Sequence[tuple[int, str, int]]


def read_publications(db: Session, *, q: str, source: int, page: int) -> PublicationPage:
    total, rows = articles.browse(
        db, q=q.strip(), source_id=source, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )
    people, events = people_and_events(db, [row.id for row in rows])
    return PublicationPage(total, rows, people, events, articles.source_counts(db))

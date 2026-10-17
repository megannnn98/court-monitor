"""What the database holds of the person a publication does not name: the clues the
pipeline read from its sentences (`unnamed_figurants`), the list entries those clues
fit (`analysis.unnamed.matching`) and the people of the database named in publications
alike. Read only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from analysis.storage.models import UnnamedFigurantRecord
from analysis.unnamed.matching import candidates


@dataclass(frozen=True)
class FigurantClue:
    """One unnamed figurant of a publication, as the pipeline read it."""

    key: str
    article_id: int
    quote: str
    span_start: int
    span_end: int
    age: int | None
    gender: str | None
    place: str
    initial: str | None
    articles: list[str]
    event_type: str
    published_at: datetime | None
    title: str
    url: str | None
    source: str


@dataclass(frozen=True)
class ListCandidate:
    """A list entry the clues fit, and the reasons the matching gave."""

    key: str
    full_name: str
    birth_date: date
    birth_place: str
    age: int
    place_match: bool
    initial_match: bool
    removed: bool
    # A person's word on this entry for this figurant: same, different or None.
    decision: str | None
    reasons: list[str]


@dataclass(frozen=True)
class ListCandidates:
    shown: list[ListCandidate]
    # Entries of that age and sex in all; `shown` are the best of them.
    total: int
    likely: int


@dataclass(frozen=True)
class PublicationPerson:
    """A person of the database named in a publication."""

    key: str
    name: str
    article_id: int
    mention_count: int
    charges: list[str]
    published_at: datetime | None


_ARTICLE = text(
    """
    SELECT a.id, a.title, a.published_at, d.canonical_url, s.name AS source
    FROM parsed_articles a
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE a.id = :article_id
    """
)


def figurant_clues(session: Session, article_id: int) -> list[FigurantClue] | None:
    """The unnamed figurants of the publication, in text order; None when no such
    publication is stored."""
    article = session.execute(_ARTICLE, {"article_id": article_id}).first()
    if article is None:
        return None
    rows = session.scalars(
        select(UnnamedFigurantRecord)
        .where(UnnamedFigurantRecord.article_id == article_id)
        .order_by(UnnamedFigurantRecord.start_offset)
    )
    return [
        FigurantClue(
            key=row.key,
            article_id=row.article_id,
            quote=row.quote,
            span_start=row.start_offset,
            span_end=row.end_offset,
            age=row.age,
            gender=row.gender,
            place=row.place,
            initial=row.initial,
            articles=[str(item) for item in row.articles or []],
            event_type=row.event_type,
            published_at=row.published_at or article.published_at,
            title=article.title or "",
            url=article.canonical_url,
            source=article.source,
        )
        for row in rows
    ]


def list_candidates(session: Session, clue: FigurantClue, *, shown: int) -> ListCandidates:
    found = candidates(session, clue, shown=shown)
    return ListCandidates(
        shown=[
            ListCandidate(
                key=item.key,
                full_name=item.full_name,
                birth_date=item.birth_date,
                birth_place=item.birth_place,
                age=item.age,
                place_match=item.place_match,
                initial_match=bool(clue.initial),
                removed=item.removed,
                decision=item.decision,
                reasons=list(item.reasons),
            )
            for item in found.shown
        ],
        total=found.total,
        likely=found.likely,
    )


_PEOPLE_OF = text(
    """
    SELECT g.key, g.name, r.article_id, count(*) AS mentions, max(a.published_at) AS published_at,
           coalesce(
               (SELECT array_agg(DISTINCT c.article ORDER BY c.article)
                FROM entity_group_charges c
                WHERE c.group_id = g.id AND c.publication_id = r.article_id),
               ARRAY[]::varchar[]) AS charges
    FROM entity_group_mentions gm
    JOIN entity_groups g ON g.id = gm.group_id
    JOIN entity_mentions m ON m.id = gm.mention_id
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    WHERE r.article_id = ANY(:article_ids)
    GROUP BY g.id, g.key, g.name, r.article_id
    ORDER BY mentions DESC, g.key
    LIMIT :limit
    """
)


def people_of_publications(
    session: Session, article_ids: Sequence[int], *, limit: int
) -> list[PublicationPerson]:
    """The people the pipeline grouped from mentions in these publications."""
    if not article_ids:
        return []
    return [
        PublicationPerson(
            key=row.key,
            name=row.name,
            article_id=row.article_id,
            mention_count=row.mentions,
            charges=list(row.charges or []),
            published_at=row.published_at,
        )
        for row in session.execute(_PEOPLE_OF, {"article_ids": list(article_ids), "limit": limit})
    ]

"""What the database holds of the person a publication does not name: the clues the
pipeline read from its sentences (`unnamed_figurants`), the list entries those clues
fit (`analysis.unnamed.matching`) and the people of the database named in publications
alike. Read only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session

from analysis import read as analysis_read
from analysis.unnamed.matching import candidates
from ingestion import read
from processing import read as identity


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


def figurant_clues(session: Session, article_id: int) -> list[FigurantClue] | None:
    """The unnamed figurants of the publication, in text order; None when no such
    publication is stored."""
    article = read.article_cards(session, [article_id]).get(article_id)
    if article is None:
        return None
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
            url=article.url,
            source=article.source_name,
        )
        for row in analysis_read.article_figurants(session, article_id)
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


def people_of_publications(
    session: Session, article_ids: Sequence[int], *, limit: int
) -> list[PublicationPerson]:
    """The people the pipeline grouped from mentions in these publications."""
    people = identity.people_in(session, article_ids, limit=limit)
    published = read.published_at(session, {person.article_id for person in people})
    return [
        PublicationPerson(
            key=person.key,
            name=person.name,
            article_id=person.article_id,
            mention_count=person.mentions,
            charges=person.charges,
            published_at=published.get(person.article_id),
        )
        for person in people
    ]

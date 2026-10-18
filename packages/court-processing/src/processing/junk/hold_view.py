"""The held articles as the console lists them (ADR 0031).

`junk_screen_holds` is processing's; the articles' titles, texts and sources are the
ingestion's, read through `ingestion.read`. A list is one view of the holds: a status,
and for the held whether a model called the article junk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from ingestion import read
from processing.junk.hold_reader import MODEL_JUNK
from processing.junk.screen import HELD

# The held are two lists: what a model read as junk waits apart, to go by one press.
MODEL_JUNK_VIEW = "model_junk"
# Characters of the article's text a card begins with.
START_CHARS = 400

_VIEW = (
    "h.status = :status AND (CAST(:model_junk AS boolean) IS NULL "
    "OR coalesce(h.reader_verdict = :junk_read, false) = CAST(:model_junk AS boolean))"
)


def _view(status: str) -> dict[str, object]:
    return {
        "status": HELD if status == MODEL_JUNK_VIEW else status,
        "model_junk": {HELD: False, MODEL_JUNK_VIEW: True}.get(status),
        "junk_read": MODEL_JUNK,
    }


@dataclass(frozen=True)
class HeldArticle:
    """A hold of the list, with its article."""

    article_id: int
    status: str
    score: float
    cutoff: float
    screen: str
    reason: str
    note: str | None
    reader_verdict: str | None
    created_at: datetime
    title: str | None
    published_at: datetime | None
    source: str
    canonical_url: str
    # The first `START_CHARS` characters of the text.
    start: str | None
    # The event types of the latest successful extraction, «, »-joined; None for none.
    events: str | None


_HOLDS = text(
    f"""
    SELECT h.article_id, h.status, h.score, h.cutoff, h.screen, h.reason, h.note,
           h.reader_verdict, h.created_at,
           (SELECT string_agg(DISTINCT e.event_type, ', ') FROM extracted_events e
            WHERE e.extraction_run_id = (
                SELECT r.id FROM article_extraction_runs r
                WHERE r.article_id = h.article_id AND r.status = 'succeeded'
                ORDER BY r.id DESC LIMIT 1)) AS events
    FROM junk_screen_holds h
    WHERE {_VIEW}
    ORDER BY h.score DESC, h.article_id
    """
)
# The articles of the list that name the same person (first name and surname, as the
# extraction normalised it) by their latest successful extraction.
_SAME_PERSON = text(
    f"""
    WITH latest AS (
        SELECT DISTINCT ON (r.article_id) r.article_id, r.id
        FROM article_extraction_runs r
        JOIN junk_screen_holds h ON h.article_id = r.article_id AND {_VIEW}
        WHERE r.status = 'succeeded'
        ORDER BY r.article_id, r.id DESC
    ), named AS (
        SELECT DISTINCT l.article_id, lower(m.normalized_text) AS name
        FROM latest l JOIN entity_mentions m ON m.extraction_run_id = l.id
        WHERE m.entity_type = 'person' AND m.normalized_text LIKE '% %'
    )
    SELECT x.article_id, y.article_id
    FROM named x JOIN named y ON y.name = x.name AND x.article_id < y.article_id
    """
)
_COUNTS = text(
    """
    SELECT CASE WHEN status = 'held' AND reader_verdict = 'junk' THEN 'model_junk' ELSE status END,
           count(*)
    FROM junk_screen_holds GROUP BY 1
    """
)
_STATUS = text("SELECT status FROM junk_screen_holds WHERE article_id = :article")


def held_articles(session: Session, status: str) -> list[HeldArticle]:
    """The list's holds, the highest score first, with their articles."""
    holds = session.execute(_HOLDS, _view(status)).all()
    articles = read.stored_articles(session, [row.article_id for row in holds])
    return [
        HeldArticle(
            article_id=row.article_id,
            status=row.status,
            score=row.score,
            cutoff=row.cutoff,
            screen=row.screen,
            reason=row.reason,
            note=row.note,
            reader_verdict=row.reader_verdict,
            created_at=row.created_at,
            title=article.title,
            published_at=article.published_at,
            source=article.source_name,
            canonical_url=article.url,
            start=None if article.text is None else article.text[:START_CHARS],
            events=row.events,
        )
        for row in holds
        if (article := articles.get(row.article_id)) is not None
    ]


def same_person_pairs(session: Session, status: str) -> list[tuple[int, int]]:
    """Pairs of the list's articles that name the same person, the lower id first."""
    return [(first, second) for first, second in session.execute(_SAME_PERSON, _view(status))]


def hold_counts(session: Session) -> dict[str, int]:
    """The holds by status, the held a model called junk apart (`MODEL_JUNK_VIEW`)."""
    return {str(key): int(value) for key, value in session.execute(_COUNTS).all()}


def hold_status(session: Session, article_id: int) -> str | None:
    """The status of the article's hold; None when it is not held."""
    status = session.scalar(_STATUS, {"article": article_id})
    return None if status is None else str(status)

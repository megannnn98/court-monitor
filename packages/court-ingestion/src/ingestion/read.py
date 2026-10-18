"""The stored articles as the stages after ingestion read them (ADR 0031).

`sources`, `source_documents` and `parsed_articles` are the ingestion's; another package
reads them here, in its own transaction (the `Session` it passes), and gets values, never
the ORM records. Every function takes many ids at once: a page or a step asks once for
what it shows, not once per row.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, bindparam, func, select, text
from sqlalchemy.orm import Session

from db.arrays import any_of
from ingestion.storage.models import ParsedArticleRecord, Source, SourceDocument


def article_count(session: Session) -> int:
    """Number of stored parsed articles, including ones not yet extracted."""
    return session.scalar(select(func.count()).select_from(ParsedArticleRecord)) or 0


@dataclass(frozen=True)
class ArticleCard:
    """What a page or a quote says of an article: where it is from and when."""

    id: int
    title: str
    url: str
    published_at: datetime | None
    source_name: str
    source_base_url: str


@dataclass(frozen=True)
class StoredArticle:
    """An article with its text, as the extraction reads it."""

    id: int
    title: str
    text: str
    published_at: datetime | None
    url: str
    source_name: str
    source_base_url: str


@dataclass(frozen=True)
class ArticleDetail:
    """Article inspection, including the source's original publication id."""

    id: int
    source_name: str
    external_id: str
    title: str
    published_at: datetime | None
    url: str
    text: str


def article_detail(session: Session, article_id: int) -> ArticleDetail | None:
    """One article with source provenance and full text; None if it is no longer stored."""
    row = session.execute(
        _joined(
            ParsedArticleRecord.id,
            Source.name,
            SourceDocument.external_id,
            ParsedArticleRecord.title,
            ParsedArticleRecord.published_at,
            SourceDocument.canonical_url,
            ParsedArticleRecord.text,
        ).where(ParsedArticleRecord.id == article_id)
    ).one_or_none()
    return None if row is None else ArticleDetail(*row)


def article_exists(session: Session, article_id: int) -> bool:
    """Whether the publication still exists, without loading its text."""
    return (
        session.scalar(select(ParsedArticleRecord.id).where(ParsedArticleRecord.id == article_id))
        is not None
    )


def _joined(*columns: Any) -> Select[Any]:
    return (
        select(*columns)
        .join(SourceDocument, ParsedArticleRecord.document_id == SourceDocument.id)
        .join(Source, SourceDocument.source_id == Source.id)
    )


_CARD = (
    ParsedArticleRecord.id,
    ParsedArticleRecord.title,
    SourceDocument.canonical_url,
    ParsedArticleRecord.published_at,
    Source.name,
    Source.base_url,
)
_STORED = (
    ParsedArticleRecord.id,
    ParsedArticleRecord.title,
    ParsedArticleRecord.text,
    ParsedArticleRecord.published_at,
    SourceDocument.canonical_url,
    Source.name,
    Source.base_url,
)


def article_cards(session: Session, ids: Collection[int]) -> dict[int, ArticleCard]:
    """The cards of the articles asked for; an id that is not stored is left out."""
    if not ids:
        return {}
    rows = session.execute(_joined(*_CARD).where(any_of(ParsedArticleRecord.id, ids))).all()
    return {row[0]: ArticleCard(*row) for row in rows}


def stored_articles(session: Session, ids: Collection[int]) -> dict[int, StoredArticle]:
    """The articles asked for, with their text; an id that is not stored is left out."""
    if not ids:
        return {}
    rows = session.execute(_joined(*_STORED).where(any_of(ParsedArticleRecord.id, ids))).all()
    return {row[0]: StoredArticle(*row) for row in rows}


def list_stored_articles(
    session: Session, *, source_name: str | None, limit: int
) -> list[StoredArticle]:
    """The first `limit` articles by id, of one source (by its name) or of all."""
    statement = _joined(*_STORED).order_by(ParsedArticleRecord.id).limit(limit)
    if source_name is not None:
        statement = statement.where(Source.name == source_name)
    return [StoredArticle(*row) for row in session.execute(statement).all()]


def article_ids(session: Session, *, source_base_url: str) -> list[int]:
    """Every stored article of one source, by its base URL, in id order."""
    return list(
        session.scalars(
            _joined(ParsedArticleRecord.id)
            .where(Source.base_url == source_base_url)
            .order_by(ParsedArticleRecord.id)
        ).all()
    )


def published_at(session: Session, ids: Collection[int]) -> dict[int, datetime | None]:
    """When each article asked for was published (None: not known)."""
    if not ids:
        return {}
    rows = session.execute(
        select(ParsedArticleRecord.id, ParsedArticleRecord.published_at).where(
            any_of(ParsedArticleRecord.id, ids)
        )
    ).all()
    return {article_id: published for article_id, published in rows}


def published_between(
    session: Session, ids: Collection[int], start: datetime, end: datetime
) -> list[int]:
    """Of the articles asked for, the ones published between `start` and `end`, both
    included, in id order."""
    if not ids:
        return []
    return list(
        session.scalars(
            select(ParsedArticleRecord.id)
            .where(
                any_of(ParsedArticleRecord.id, ids),
                ParsedArticleRecord.published_at.between(start, end),
            )
            .order_by(ParsedArticleRecord.id)
        ).all()
    )


def published_matching(
    session: Session, start: datetime, end: datetime, pattern: str, *, other_than: int
) -> list[int]:
    """The articles published between `start` and `end`, both included, whose lower-cased
    text matches the POSIX regular expression `pattern`, but `other_than`; by id."""
    return list(
        session.scalars(
            select(ParsedArticleRecord.id)
            .where(
                ParsedArticleRecord.id != other_than,
                ParsedArticleRecord.published_at.between(start, end),
                func.lower(ParsedArticleRecord.text).op("~")(bindparam("pattern", pattern)),
            )
            .order_by(ParsedArticleRecord.id)
        ).all()
    )


@dataclass(frozen=True)
class TextWindow:
    """A slice of an article's text: `length` characters from `start` (0-based, as the
    offsets of mentions and events are)."""

    article_id: int
    start: int
    length: int


_WINDOWS = text(
    """
    SELECT w.ordinal, substr(a.text, w.start + 1, w.length)
    FROM unnest(CAST(:articles AS integer[]), CAST(:starts AS integer[]),
                CAST(:lengths AS integer[]))
         WITH ORDINALITY AS w(article_id, start, length, ordinal)
    JOIN parsed_articles a ON a.id = w.article_id
    """
)


def text_windows(session: Session, windows: Sequence[TextWindow]) -> list[str | None]:
    """The text of each window, in the order asked; None where the article is gone. A
    window past the end of the text is cut short, as SQL `substr` cuts it."""
    if not windows:
        return []
    rows = session.execute(
        _WINDOWS,
        {
            "articles": [window.article_id for window in windows],
            "starts": [window.start for window in windows],
            "lengths": [max(window.length, 0) for window in windows],
        },
    ).all()
    found: list[str | None] = [None] * len(windows)
    for ordinal, value in rows:
        found[ordinal - 1] = value
    return found


def text_pattern(session: Session, ids: Collection[int], pattern: str) -> dict[int, str | None]:
    """The first group of the POSIX regular expression `pattern` in each article's text
    (SQL `substring(text from pattern)`), None where it does not match."""
    if not ids:
        return {}
    rows = session.execute(
        select(
            ParsedArticleRecord.id,
            func.substring(ParsedArticleRecord.text, bindparam("pattern", pattern)),
        ).where(any_of(ParsedArticleRecord.id, ids))
    ).all()
    return {article_id: value for article_id, value in rows}


def articles_containing(
    session: Session, stems: Sequence[str], *, limit: int
) -> list[StoredArticle]:
    """The articles whose text (folded: lower case, «ё» as «е») holds every stem, the
    latest first (unknown dates last), then the higher id."""
    if not stems:
        return []
    folded = func.replace(func.lower(ParsedArticleRecord.text), "ё", "е")
    rows = session.execute(
        _joined(*_STORED)
        .where(*(folded.like(f"%{stem}%") for stem in stems))
        .order_by(
            ParsedArticleRecord.published_at.desc().nullslast(), ParsedArticleRecord.id.desc()
        )
        .limit(limit)
    ).all()
    return [StoredArticle(*row) for row in rows]


_CONTAINING = text(
    """
    SELECT id FROM parsed_articles
    WHERE id = ANY(:ids) AND position(:needle IN regexp_replace(text, '\\s+', ' ', 'g')) > 0
    """
)


def texts_containing(session: Session, ids: Collection[int], needle: str) -> set[int]:
    """Of the articles asked for, the ones whose text holds `needle` once its runs of
    white space are single spaces."""
    if not ids or not needle:
        return set()
    return set(session.scalars(_CONTAINING, {"ids": sorted(set(ids)), "needle": needle}))


@dataclass(frozen=True)
class ListedArticle:
    """A row of the list of stored publications («Публикации»)."""

    id: int
    title: str
    published_at: datetime | None
    source: str
    canonical_url: str


_BROWSE_FILTER = """
    FROM parsed_articles a
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE (:q = '' OR a.title ILIKE :pattern OR a.text ILIKE :pattern)
      AND (:source = 0 OR s.id = :source)
"""
_BROWSE_COUNT = text(f"SELECT count(*) {_BROWSE_FILTER}")
_BROWSE_ROWS = text(
    f"""
    SELECT a.id, a.title, a.published_at, s.name AS source, d.canonical_url {_BROWSE_FILTER}
    ORDER BY a.published_at DESC NULLS LAST, a.id DESC
    LIMIT :limit OFFSET :offset
    """
)
_SOURCE_COUNTS = text(
    """
    SELECT s.id, s.name, count(*) FROM parsed_articles a
    JOIN source_documents d ON d.id = a.document_id JOIN sources s ON s.id = d.source_id
    GROUP BY s.id, s.name ORDER BY s.name
    """
)


def browse(
    session: Session, *, q: str, source_id: int, limit: int, offset: int
) -> tuple[int, list[ListedArticle]]:
    """How many publications hold `q` in the title or the text (any, when empty), of one
    source (0: all), and one page of them, the latest first."""
    params = {"q": q, "pattern": f"%{q}%", "source": source_id}
    total = session.scalar(_BROWSE_COUNT, params) or 0
    rows = session.execute(_BROWSE_ROWS, {**params, "limit": limit, "offset": offset}).all()
    return total, [ListedArticle(*row) for row in rows]


def source_counts(session: Session) -> list[tuple[int, str, int]]:
    """(id, name, publications) of every source with a publication, by name."""
    return [(row[0], row[1], row[2]) for row in session.execute(_SOURCE_COUNTS).all()]


def texts_with(session: Session, ids: Collection[int], fragment: str) -> set[int]:
    """Of the articles asked for, the ones whose text holds `fragment` as it is (SQL
    `LIKE '%fragment%'`: `%` and `_` in it are wildcards)."""
    if not ids:
        return set()
    return set(
        session.scalars(
            select(ParsedArticleRecord.id).where(
                any_of(ParsedArticleRecord.id, ids),
                ParsedArticleRecord.text.like(f"%{fragment}%"),
            )
        )
    )


_PUBLISHED_DAYS = text("SELECT id, published_at::date FROM parsed_articles WHERE id = ANY(:ids)")
_PUBLISHED_ON = text(
    "SELECT id, published_at::date FROM parsed_articles WHERE published_at::date = ANY(:days)"
)


def published_days(session: Session, ids: Collection[int]) -> dict[int, date | None]:
    """The day each article asked for was published, in the database's time zone."""
    if not ids:
        return {}
    return dict(session.execute(_PUBLISHED_DAYS, {"ids": sorted(set(ids))}).tuples().all())


def published_on(session: Session, days: Collection[date]) -> dict[int, date]:
    """Every article published on one of these days (the database's time zone)."""
    if not days:
        return {}
    return dict(session.execute(_PUBLISHED_ON, {"days": sorted(set(days))}).tuples().all())


def published_before(session: Session, since: datetime, *, limit: int | None) -> list[int]:
    """The articles published before `since`, in id order, at most `limit` of them."""
    query = (
        select(ParsedArticleRecord.id)
        .where(ParsedArticleRecord.published_at < since)
        .order_by(ParsedArticleRecord.id)
        .limit(limit)
    )
    return list(session.scalars(query))


__all__: Sequence[str] = (
    "ArticleCard",
    "ListedArticle",
    "StoredArticle",
    "TextWindow",
    "article_cards",
    "article_ids",
    "articles_containing",
    "browse",
    "list_stored_articles",
    "published_at",
    "published_before",
    "published_between",
    "published_days",
    "published_matching",
    "published_on",
    "source_counts",
    "stored_articles",
    "text_pattern",
    "text_windows",
    "texts_containing",
    "texts_with",
)


def live_document_count(session: Session, *, expired_type: str) -> int:
    """The documents stored, but the tombstones of `expired_type`."""
    return (
        session.scalar(
            select(func.count())
            .select_from(SourceDocument)
            .where(SourceDocument.content_type != expired_type)
        )
        or 0
    )


def published_span(session: Session) -> tuple[datetime | None, datetime | None]:
    """The dates of the first and the latest article."""
    first, last = session.execute(
        select(
            func.min(ParsedArticleRecord.published_at), func.max(ParsedArticleRecord.published_at)
        )
    ).one()
    return first, last


_SIMILAR_TITLES = text(
    """
    WITH titled AS (
        SELECT a.id, left(a.title, 300) AS title FROM parsed_articles a
        WHERE a.id = ANY(:ids) AND a.title IS NOT NULL AND length(a.title) >= :shortest
    )
    SELECT x.id, y.id FROM titled x JOIN titled y ON x.id < y.id
    WHERE similarity(x.title, y.title) >= :similarity
    """
)


def similar_titles(
    session: Session, ids: Collection[int], *, similarity: float, shortest: int = 20
) -> list[tuple[int, int]]:
    """Pairs of the articles asked for whose titles (their first 300 characters, those of
    at least `shortest`) are nearly the same text: trigram similarity (pg_trgm) of at
    least `similarity`. The lower id first."""
    if not ids:
        return []
    return [
        (first, second)
        for first, second in session.execute(
            _SIMILAR_TITLES, {"ids": list(ids), "similarity": similarity, "shortest": shortest}
        )
    ]

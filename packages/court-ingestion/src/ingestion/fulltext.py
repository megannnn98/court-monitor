"""Ranked search of the stored publications by their words: PostgreSQL full-text search
over `parsed_articles.search_vector` (`to_tsvector('russian', text)`, GIN-indexed).

The Russian stemmer reads a declined name as its stem, so «Марии Бонцлер» finds «Мария
Бонцлер» and «Сидорова» finds «Сидоров»; the order of the words does not matter. Read
only: the ingestion's public search of its articles (ADR 0031).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

# Characters of an article's text kept on each side of the word quoted.
QUOTE_CONTEXT = 160
# ts_rank_cd's normalization 32: rank / (rank + 1), so a score is in [0, 1).
_RANK_NORMALIZATION = 32
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_MIN_STEM = 4


@dataclass(frozen=True)
class RankedPublication:
    article_id: int
    title: str
    url: str | None
    source: str
    published_at: datetime | None
    quote: str
    span_start: int
    span_end: int
    # ts_rank_cd, normalized: comparable within one search only.
    rank: float


def _fold(value: str) -> str:
    return value.lower().replace("ё", "е")


def _stems(words: Sequence[str]) -> list[str]:
    """Beginnings to find a word of the query in the text, however it is declined."""
    stems = []
    for word in words:
        folded = _fold(word)
        if len(folded) < 2:
            continue
        stems.append(
            folded[: max(_MIN_STEM, len(folded) - 2)] if len(folded) > _MIN_STEM else folded
        )
    return stems


def _quote(body: str, stems: Sequence[str]) -> tuple[str, int, int]:
    folded = _fold(body)
    positions = [position for stem in stems if (position := folded.find(stem)) >= 0]
    at = min(positions) if positions else 0
    start = max(at - QUOTE_CONTEXT, 0)
    end = min(at + QUOTE_CONTEXT, len(body))
    return body[start:end], start, end


def _combined(parts: Sequence[str]) -> str:
    """The name's query and the terms' query, both required; terms that are only stop
    words («и», «по») leave the name alone rather than nothing."""
    if len(parts) == 1:
        return parts[0]
    name, terms = parts
    return f"CASE WHEN {terms} IS NULL THEN {name} ELSE {name} && {terms} END"


def search_publications(
    session: Session,
    *,
    name: str = "",
    any_of: Sequence[str] = (),
    limit: int,
    published_from: datetime | None = None,
    published_to: datetime | None = None,
) -> list[RankedPublication]:
    """Publications holding every word of `name` and at least one of `any_of`, the best
    ranked first. Either may be empty, not both."""
    name_words = [word for word in _WORD.findall(name) if len(word) >= 2]
    terms = [term.strip() for term in any_of if term.strip()]
    if not name_words and not terms:
        return []
    parts: list[str] = []
    params: dict[str, object] = {"limit": limit, "norm": _RANK_NORMALIZATION}
    if name_words:
        parts.append("plainto_tsquery('russian', :name)")
        params["name"] = " ".join(name_words)
    if terms:
        # Any of the terms, each by the beginnings of its stems: «арест» finds
        # «арестовал», whose stem the stemmer cuts differently. Each lexeme is quoted, so
        # no word of a term is read as an operator of the query.
        parts.append(
            """(SELECT to_tsquery('simple', string_agg('(' || term_query || ')', ' | '))
                FROM (SELECT string_agg(quote_literal(v.lexeme) || ':*', ' & ') AS term_query
                      FROM unnest(CAST(:terms AS text[])) WITH ORDINALITY AS t(term, n)
                      CROSS JOIN LATERAL unnest(to_tsvector('russian', t.term)) AS v
                      GROUP BY t.n) AS each_term)"""
        )
        params["terms"] = terms
    where = ["a.search_vector @@ q.query"]
    if published_from is not None:
        where.append("a.published_at >= :published_from")
        params["published_from"] = published_from
    if published_to is not None:
        where.append("a.published_at <= :published_to")
        params["published_to"] = published_to
    statement = text(
        f"""
        WITH q AS (SELECT {_combined(parts)} AS query)
        SELECT a.id AS article_id, a.title, d.canonical_url, s.name AS source,
               a.published_at, a.text,
               ts_rank_cd(a.search_vector, q.query, :norm) AS rank
        FROM q, parsed_articles a
        JOIN source_documents d ON d.id = a.document_id
        JOIN sources s ON s.id = d.source_id
        WHERE {" AND ".join(where)}
        ORDER BY rank DESC, a.published_at DESC NULLS LAST, a.id DESC
        LIMIT :limit
        """
    )
    # The longest word of the name is the likeliest surname: quote the text around it.
    stems = _stems(sorted(name_words, key=len, reverse=True)[:1] or terms)
    found = []
    for row in session.execute(statement, params):
        quote, start, end = _quote(row.text or "", stems)
        found.append(
            RankedPublication(
                article_id=row.article_id,
                title=row.title or "",
                url=row.canonical_url,
                source=row.source,
                published_at=row.published_at,
                quote=quote,
                span_start=start,
                span_end=end,
                rank=float(row.rank),
            )
        )
    return found


# The marks around a found word: the page turns them into a highlight.
MARK_OPEN, MARK_CLOSE = "[[", "]]"
_HEADLINE = f"StartSel={MARK_OPEN}, StopSel={MARK_CLOSE}, MaxWords=40, MinWords=15, MaxFragments=2"


@dataclass(frozen=True)
class Headline:
    """A publication found by its words, with the fragments that hold them marked."""

    article_id: int
    title: str | None
    source: str
    published_at: datetime | None
    snippet: str


def headlines(session: Session, words: Sequence[str], *, limit: int) -> tuple[int, list[Headline]]:
    """How many publications hold any of the words, and the `limit` best ranked: the more
    of the words, the higher (then the latest, then the lower id)."""
    query = " || ".join(f"plainto_tsquery('russian', :w{index})" for index in range(len(words)))
    rows = session.execute(
        text(
            f"""
            SELECT a.id, a.title, a.published_at, s.name AS source,
                   ts_headline('russian', a.text, q.query, :headline) AS snippet,
                   count(*) OVER () AS total
            FROM parsed_articles a
            JOIN source_documents d ON d.id = a.document_id
            JOIN sources s ON s.id = d.source_id
            CROSS JOIN (SELECT {query} AS query) q
            WHERE a.search_vector @@ q.query
            ORDER BY ts_rank(a.search_vector, q.query) DESC, a.published_at DESC NULLS LAST, a.id
            LIMIT :found
            """
        ),
        {
            **{f"w{index}": word for index, word in enumerate(words)},
            "headline": _HEADLINE,
            "found": limit,
        },
    ).all()
    return (
        rows[0].total if rows else 0,
        [Headline(row.id, row.title, row.source, row.published_at, row.snippet) for row in rows],
    )

"""People mentioned in the news of a period: one query, no N+1.

The path is persons → resolved mentions (and person↔event links) → extraction runs →
parsed articles → source documents → sources, filtered by the article's `published_at`
and by the news sources of the registry. The two paths are UNIONed, which deduplicates
`(person_id, article_id)`: several mentions, several events or several aliases of one
person in one article count as one article.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, Select, distinct, func, select, true, union
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session, sessionmaker

from db.models.extraction import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    ExtractedEventRecord,
)
from db.models.persons import PersonEventLinkRecord, PersonRecord
from db.models.sources import ParsedArticleRecord, Source, SourceDocument
from persons.models import PersonStatus
from sources.source_registry import news_source_base_urls
from telegram_bot.models import NewsArticleReference, PeopleFromNewsResult, PersonFromNews

# Enough to show what the person is in the news for; `article_count` keeps the total.
ARTICLES_PER_PERSON = 3


class PeopleFromNewsRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        news_base_urls: Sequence[str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        # The registry of news sources, by the base URL under which `sources` stores them.
        self._news_base_urls = list(
            news_base_urls if news_base_urls is not None else news_source_base_urls()
        )

    def people_in_period(
        self, *, date_from: date, date_to: date, start: datetime, end: datetime, limit: int
    ) -> PeopleFromNewsResult:
        """`start` is inclusive and `end` exclusive; the caller converts them to UTC."""
        with self._session_factory() as session:
            rows = session.execute(self._query(start, end, limit)).all()
        return PeopleFromNewsResult(
            date_from=date_from,
            date_to=date_to,
            total=int(rows[0].total) if rows else 0,
            limit=limit,
            people=_to_people(rows),
        )

    def _query(self, start: datetime, end: datetime, limit: int) -> Select[Any]:
        pairs = self._person_article_pairs(start, end)
        stats = (
            select(
                pairs.c.person_id,
                pairs.c.canonical_name,
                func.count().label("article_count"),
                func.max(pairs.c.published_at).label("latest_published_at"),
                func.array_agg(
                    aggregate_order_by(distinct(pairs.c.source_name), pairs.c.source_name.asc())
                ).label("sources"),
            )
            .group_by(pairs.c.person_id, pairs.c.canonical_name)
            .cte("person_stats")
        )
        # `total` is the number of people before the limit; `position` is the page order.
        ranked = select(
            stats,
            func.count().over().label("total"),
            func.row_number()
            .over(
                order_by=(
                    stats.c.latest_published_at.desc(),
                    stats.c.canonical_name.asc(),
                    stats.c.person_id.asc(),
                )
            )
            .label("position"),
        ).cte("ranked_people")
        page = select(ranked).where(ranked.c.position <= limit).cte("page")
        # LATERAL: the newest articles of the people on this page only.
        articles = (
            select(
                pairs.c.article_id,
                pairs.c.title,
                pairs.c.url,
                pairs.c.source_name,
                pairs.c.published_at,
            )
            .where(pairs.c.person_id == page.c.person_id)
            .order_by(pairs.c.published_at.desc(), pairs.c.article_id.desc())
            .limit(ARTICLES_PER_PERSON)
            .lateral("top_articles")
        )
        return (
            select(
                page.c.person_id,
                page.c.canonical_name,
                page.c.article_count,
                page.c.latest_published_at,
                page.c.sources,
                page.c.total,
                page.c.position,
                articles.c.article_id,
                articles.c.title,
                articles.c.url,
                articles.c.source_name,
                articles.c.published_at,
            )
            .select_from(page)
            .join(articles, true())
            .order_by(
                page.c.position,
                articles.c.published_at.desc(),
                articles.c.article_id.desc(),
            )
        )

    def _person_article_pairs(self, start: datetime, end: datetime) -> Any:
        """Distinct (person, article) pairs: the mention path and the event path, UNIONed."""
        columns = (
            PersonRecord.id.label("person_id"),
            PersonRecord.canonical_name.label("canonical_name"),
            ParsedArticleRecord.id.label("article_id"),
            ParsedArticleRecord.title.label("title"),
            ParsedArticleRecord.published_at.label("published_at"),
            SourceDocument.canonical_url.label("url"),
            Source.name.label("source_name"),
        )
        conditions = (
            PersonRecord.status == PersonStatus.ACTIVE.value,
            # An article without a publication date never belongs to a period.
            ParsedArticleRecord.published_at.is_not(None),
            ParsedArticleRecord.published_at >= start,
            ParsedArticleRecord.published_at < end,
            Source.base_url.in_(self._news_base_urls),
        )
        # A mention carries `person_id` only once entity resolution linked it.
        through_mentions = (
            select(*columns)
            .join(EntityMentionRecord, EntityMentionRecord.person_id == PersonRecord.id)
            .join(
                ArticleExtractionRunRecord,
                ArticleExtractionRunRecord.id == EntityMentionRecord.extraction_run_id,
            )
            .join(
                ParsedArticleRecord,
                ParsedArticleRecord.id == ArticleExtractionRunRecord.article_id,
            )
            .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
            .join(Source, Source.id == SourceDocument.source_id)
            .where(*conditions)
        )
        through_events = (
            select(*columns)
            .join(PersonEventLinkRecord, PersonEventLinkRecord.person_id == PersonRecord.id)
            .join(ExtractedEventRecord, ExtractedEventRecord.id == PersonEventLinkRecord.event_id)
            .join(
                ArticleExtractionRunRecord,
                ArticleExtractionRunRecord.id == ExtractedEventRecord.extraction_run_id,
            )
            .join(
                ParsedArticleRecord,
                ParsedArticleRecord.id == ArticleExtractionRunRecord.article_id,
            )
            .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
            .join(Source, Source.id == SourceDocument.source_id)
            .where(*conditions)
        )
        # UNION, not UNION ALL: it is what deduplicates the pairs.
        return union(through_mentions, through_events).cte("person_article")


def _to_people(rows: Sequence[Row[Any]]) -> list[PersonFromNews]:
    """Rows arrive grouped by person, in page order, articles newest first."""
    people: list[PersonFromNews] = []
    for row in rows:
        if not people or people[-1].person_id != row.person_id:
            people.append(
                PersonFromNews(
                    person_id=row.person_id,
                    canonical_name=row.canonical_name,
                    article_count=row.article_count,
                    latest_published_at=row.latest_published_at,
                    sources=list(row.sources),
                    articles=[],
                )
            )
        people[-1].articles.append(
            NewsArticleReference(
                article_id=row.article_id,
                title=row.title,
                url=row.url,
                source_name=row.source_name,
                published_at=row.published_at,
            )
        )
    return people

"""What the bot shows: application models, never ORM rows."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class NewsArticleReference(BaseModel):
    article_id: int
    title: str
    url: str
    source_name: str
    published_at: datetime


class PersonFromNews(BaseModel):
    person_id: int
    canonical_name: str
    article_count: int
    latest_published_at: datetime
    sources: list[str]
    # The most recent articles only; `article_count` counts them all.
    articles: list[NewsArticleReference]


class PeopleFromNewsResult(BaseModel):
    date_from: date
    date_to: date
    total: int
    limit: int
    people: list[PersonFromNews]

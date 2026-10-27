"""Held publications and junk-screen actions."""

from datetime import datetime

from pydantic import BaseModel, Field

from .common import OptionResponse


class HeldArticleResponse(BaseModel):
    """An article the junk screen held back from the purge."""

    article_id: int
    # "held", "released" or "junk".
    status: str
    title: str | None
    published_at: datetime | None
    source: str
    # The publication's own address, for a link: http(s) only.
    url: str | None
    score: float
    cutoff: float
    events: str | None
    start: str
    reason: str
    # A model's word on it, with whose word it is.
    note: str | None
    note_label: str


class HeldStoryResponse(BaseModel):
    """Articles that seem to tell one story; one article alone is a story of one."""

    articles: list[HeldArticleResponse]


class JunkHoldsResponse(BaseModel):
    """«Отсев»: one list of held articles, a page of its stories, and every list's count."""

    status: str
    statuses: list[OptionResponse]
    stories: list[HeldStoryResponse]
    # Every article of the list, for «Мусор — все» on the model's list.
    article_ids: list[int]
    page: int
    pages: int


class HeldArticleRequest(BaseModel):
    article: int = Field(ge=1)


class HeldArticlesRequest(BaseModel):
    # No upper bound: «Мусор — все» sends every article of the list, as the legacy form
    # does, and takes all of them or none.
    articles: list[int] = Field(min_length=1)


class HeldActionResponse(BaseModel):
    articles: list[int]
    # «Извлечь заново» found an event: the article went back to work.
    released: bool = False

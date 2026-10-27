"""Overview and wiki responses."""

from datetime import datetime

from pydantic import BaseModel


class WikiPageLinkResponse(BaseModel):
    slug: str


class WikiPageResponse(BaseModel):
    """A wiki page as HTML: CommonMark from the repository's own docs, raw HTML in it shown
    as text, links to other pages on `/wiki/`."""

    slug: str
    html: str


class OverviewNewsResponse(BaseModel):
    key: str
    name: str
    published_at: datetime | None
    reason: str


class OverviewUnnamedResponse(BaseModel):
    key: str
    quote: str
    published_at: datetime | None
    facts: str
    found: str


class OverviewResponse(BaseModel):
    """«Обзор»: the political cases' new cases and sentences, the unnamed to identify, and
    what waits for the operator."""

    source_errors: int
    new_cases: int
    latest_new_cases: list[OverviewNewsResponse]
    sentences: int
    latest_sentences: list[OverviewNewsResponse]
    unnamed: int
    latest_unnamed: list[OverviewUnnamedResponse]
    pairs: int
    unclear_roles: int
    unclear_verdicts: int

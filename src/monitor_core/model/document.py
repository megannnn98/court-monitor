from datetime import datetime

from pydantic import BaseModel


class RawDocument(BaseModel):
    external_id: str
    url: str
    fetched_at: datetime
    content_type: str
    content: bytes


class ParsedArticle(BaseModel):
    external_id: str
    url: str
    title: str
    published_at: datetime | None
    text: str


class IngestionResult[R](BaseModel):
    """An article and what its storage made of it: `R` is the storage's own result."""

    article: ParsedArticle
    persistence: R

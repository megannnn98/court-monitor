from datetime import datetime

from pydantic import BaseModel


class SourceReference(BaseModel):
    external_id: str
    url: str


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


class ArticleChunk(BaseModel):
    ordinal: int
    text: str

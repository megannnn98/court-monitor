from datetime import datetime

from pydantic import BaseModel, Field, field_validator


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


class PersistenceResult(BaseModel):
    document_id: int
    chunks_saved: int


class IngestionResult(BaseModel):
    article: ParsedArticle
    chunks: list[ArticleChunk]
    persistence: PersistenceResult


class SearchQuery(BaseModel):
    text: str
    limit: int = Field(default=10, ge=1, le=100)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("search text must not be blank")
        return value


class SearchHit(BaseModel):
    chunk_id: int
    article_id: int
    source_base_url: str
    external_id: str
    ordinal: int
    title: str
    published_at: datetime | None
    url: str
    text: str
    score: float

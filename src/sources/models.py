from datetime import datetime

from pydantic import BaseModel, Field, field_validator


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
    article_id: int
    source_base_url: str
    external_id: str
    title: str
    published_at: datetime | None
    url: str
    text: str
    score: float

"""Investigation search and question responses."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class InvestigationFoundResponse(BaseModel):
    key: str
    # Surname first.
    name: str
    role_label: str | None
    article_count: int
    last_published_at: datetime | None


class InvestigationSearchResponse(BaseModel):
    """«Найти человека»: by any form of the name, or the latest political cases."""

    heading: str
    items: list[InvestigationFoundResponse]


class AskedQuestionResponse(BaseModel):
    id: int
    question: str
    asked_at: datetime


class AskAnswerResponse(BaseModel):
    """A question asked, its answer and the counts it stands on."""

    id: int
    question: str
    answer: str
    # "answered", "refused", "failed" (`results.ask`).
    outcome: str
    asked_at: datetime
    model: str
    cost_usd: float
    # The answer's numbers no count holds: to be checked by eye.
    unverified: list[str]
    # Each count as the model's call returned it: `tool` is "stats", "list" or "search".
    results: list[dict[str, Any]]


class AskPageResponse(BaseModel):
    """«Спросить»: the day's spending, the examples, the chosen answer and the questions
    asked before."""

    spent_today: float
    budget: float
    max_question: int
    examples: list[str]
    # Marks around the words found in a search's snippet.
    mark_open: str
    mark_close: str
    answer: AskAnswerResponse | None
    history: list[AskedQuestionResponse]


class AskRequest(BaseModel):
    question: str = Field(max_length=2000)


class AskResponse(BaseModel):
    # None when nothing was asked: `note` says why.
    id: int | None
    note: str | None

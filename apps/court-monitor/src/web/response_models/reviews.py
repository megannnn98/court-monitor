"""Operator review queues and decisions."""

from pydantic import BaseModel

from .common import NameFormResponse, OptionResponse
from .entities_publications import EntityArticleResponse


class ReviewResponse(BaseModel):
    """Review response model."""

    id: int
    subject_type: str
    subject_id: int
    decision: str | None = None
    confidence: float | None = None
    reason: str | None = None
    reviewer_note: str | None = None
    created_at: str
    reviewed_at: str | None = None


class ReviewItemResponse(BaseModel):
    key: str
    # Surname first.
    name: str
    # Why the step could not decide.
    reason: str


class ReviewListResponse(BaseModel):
    """A review station: the people a step could not decide, and the operator's choices."""

    title: str
    explanation: str
    choices: list[OptionResponse]
    items: list[ReviewItemResponse]


class RoleDecisionRequest(BaseModel):
    key: str
    role: str


class VerdictDecisionRequest(BaseModel):
    key: str
    verdict: str


class DecisionResponse(BaseModel):
    key: str
    decision: str


class PairSideResponse(BaseModel):
    key: str
    name: str
    role_label: str | None
    rf_label: str | None
    variants: list[NameFormResponse]
    mention_count: int
    article_count: int
    regions: list[str]
    articles: list[EntityArticleResponse]


class PairResponse(BaseModel):
    kind: str
    hint: str
    # Why the check did not merge it itself, when a side could be several people.
    note: str | None
    left: PairSideResponse
    right: PairSideResponse


class PairListResponse(BaseModel):
    """«Пары»: two entities that may be one person, for the operator to decide."""

    kinds: list[OptionResponse]
    open_pairs: int
    items: list[PairResponse]
    total: int
    page: int
    page_size: int
    decided: dict[str, int]


class PairDecisionRequest(BaseModel):
    key_a: str
    key_b: str
    decision: str

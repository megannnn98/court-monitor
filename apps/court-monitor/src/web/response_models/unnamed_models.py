"""Unnamed figurants and operator-base decisions."""

from datetime import date, datetime

from pydantic import BaseModel, Field

from .common import OptionResponse


class UnnamedRfCandidateResponse(BaseModel):
    """An entry of the list that may be the unnamed figurant."""

    key: str
    full_name: str
    # What «Это он» names the person: the entry's name, capitalised.
    display_name: str
    rf_name: str
    birth_date: date
    birth_place: str
    reasons: list[str]
    # Since when the list holds the entry, or that it no longer does.
    seen: str
    decision: str | None


class UnnamedNamedCandidateResponse(BaseModel):
    """A named figurant of another publication who may be the unnamed one."""

    key: str
    # The key «Не он» is said of.
    candidate_key: str
    name: str
    article_id: int
    title: str
    start: int
    end: int
    reasons: list[str]
    decision: str | None


class UnnamedBaseCandidateResponse(BaseModel):
    """A person of the operator's base who may be the unnamed figurant."""

    key: str
    name: str
    birth_date: date
    place: str
    articles: str
    reasons: list[str]
    decision: str | None


class CourtHintResponse(BaseModel):
    court: str
    cases: int
    site: str | None


class UnnamedCardResponse(BaseModel):
    """One unnamed figurant: the sentence, what it tells, the operator's word and the
    candidates from the list, other publications and the base."""

    key: str
    article_id: int
    start_offset: int
    end_offset: int
    quote: str
    published_at: datetime | None
    facts: str
    explanation: str
    has_age: bool
    # The operator's word: "" none, or the resolution (`analysis.unnamed.figurants`).
    resolution: str
    identified_as: str | None
    # How it was identified, and when, said in words.
    note: str | None
    rf_candidates: list[UnnamedRfCandidateResponse]
    rf_total: int
    rf_snapshot_date: datetime | None
    named_candidates: list[UnnamedNamedCandidateResponse]
    base_candidates: list[UnnamedBaseCandidateResponse]
    base_total: int
    courts: list[CourtHintResponse]
    courts_total: int


class UnnamedScreenedResponse(BaseModel):
    """Whom the search set aside on its own."""

    key: str
    article_id: int
    start_offset: int
    end_offset: int
    quote: str
    published_at: datetime | None
    facts: str
    reason: str
    explanation: str


class UnnamedRfEntryResponse(BaseModel):
    # None for an entry without a birth date: it cannot be matched.
    key: str | None
    full_name: str
    birth_date: date | None


class UnnamedReverseResponse(BaseModel):
    key: str
    quote: str
    facts: str


class PersonChoiceResponse(BaseModel):
    key: str
    name: str


class UnnamedListResponse(BaseModel):
    """«Безымянные»: one tab of cards, the tabs' counts, and the searches' answers."""

    statuses: list[OptionResponse]
    items: list[UnnamedCardResponse]
    total: int
    page: int
    page_size: int
    # Nobody at all yet, as opposed to nobody in this tab.
    none_yet: bool
    people: list[PersonChoiceResponse]
    rf_entries: list[UnnamedRfEntryResponse]
    reverse: list[UnnamedReverseResponse]
    screened: list[UnnamedScreenedResponse]


class UnnamedActionRequest(BaseModel):
    """A word on an unnamed figurant; the fields the action needs, as the legacy form
    sends them."""

    figurant: str = Field(max_length=200)
    candidate: str = Field(default="", max_length=600)
    undo: bool = False
    resolution: str = Field(default="", max_length=40)
    normalized_name: str = Field(default="", max_length=200)
    existing_person_key: str = Field(default="", max_length=600)
    rf_name: str = Field(default="", max_length=600)
    rf_birth_date: str = Field(default="", max_length=10)


class UnnamedActionResponse(BaseModel):
    figurant: str


class BaseUnnamedCandidateResponse(BaseModel):
    """An entry of the list that may be a nameless record of the base."""

    key: str
    full_name: str
    birth_date: date
    birth_place: str
    reasons: list[str]
    decision: str | None


class BaseUnnamedCardResponse(BaseModel):
    """A nameless record of the operator's base and who on the list it may be."""

    record: str
    full_name: str
    facts: str
    # "identified", "confirmed" (the entry said is no longer a candidate), "open" or
    # "rejected" (every candidate refused).
    state: str
    identified_as: str | None
    # The entry said, as its key: what «Отменить решение» takes back.
    confirmed: str | None
    candidates: list[BaseUnnamedCandidateResponse]
    total: int
    courts: list[CourtHintResponse]
    courts_total: int


class BaseUnnamedListResponse(BaseModel):
    statuses: list[OptionResponse]
    items: list[BaseUnnamedCardResponse]
    total: int
    page: int
    page_size: int
    none_yet: bool


class BaseUnnamedDecisionRequest(BaseModel):
    record: str = Field(max_length=200)
    candidate: str = Field(max_length=600)
    # "same", "different" or "clear".
    decision: str = Field(max_length=16)


class BaseUnnamedDecisionResponse(BaseModel):
    record: str

"""Dossier evidence, charges and timeline responses."""

from datetime import datetime

from pydantic import BaseModel

from .common import EventCountResponse, NameFormResponse, PersonLinkResponse


class RfEntryResponse(BaseModel):
    level: str
    # «ФИО с отчеством» or «имя и фамилия».
    level_label: str
    text: str


class DossierKnownResponse(BaseModel):
    """What the operator's base says, by the name alone; `loaded` False: no base to ask."""

    loaded: bool
    not_in_base: bool
    label: str | None
    names: list[str]
    more: int


class DossierNewsResponse(BaseModel):
    kind: str
    label: str
    reason: str


class ChargeResponse(BaseModel):
    article: str
    parts: list[str]
    shared: bool
    political: bool
    publications: int


class QuoteResponse(BaseModel):
    """An excerpt with the mention or the event's trigger at [start, end); the article
    text offsets open the full text with the same mark."""

    article_id: int
    title: str
    source: str
    published_at: datetime | None
    quote: str
    start: int
    end: int
    text_start: int
    text_end: int


class OrgResponse(BaseModel):
    name: str
    role_label: str


class TimelineItemResponse(BaseModel):
    event_type: str
    label: str
    # The event's own date where the text gave one; otherwise its publication's. `dated`
    # says which: False means `day` is the publication's, the event's date is unknown.
    day: datetime | None
    dated: bool
    confidence: float | None
    extractor_label: str
    articles: list[str]
    orgs: list[OrgResponse]
    sources: list[QuoteResponse]


class EvidenceResponse(QuoteResponse):
    url: str
    identification: str | None
    events: list[str]
    articles: list[str]
    others: list[PersonLinkResponse]


class RelatedPersonResponse(BaseModel):
    key: str
    name: str
    shared: int


class DossierResponse(BaseModel):
    """The legacy dossier: who, the system's decisions with their reasons and quotes, the
    list, the charges, the timeline and the publications behind each conclusion."""

    key: str
    name: str
    # The name as it is kept and corrected: «Имя [Отчество] Фамилия».
    name_as_kept: str
    # A person marked the entity an official, or the texts name it so.
    official: bool
    role: str | None
    role_label: str | None
    role_method_label: str | None
    role_reason: str | None
    role_quote: str | None
    verdict: str | None
    verdict_label: str | None
    verdict_method_label: str | None
    verdict_reason: str | None
    verdict_quote: str | None
    verdict_source_article_id: int | None
    rf_label: str
    rf_maybe: bool
    rf_entries: list[RfEntryResponse]
    rf_expected: str | None
    snapshot_date: datetime | None
    inclusion_attribution: str
    disputes: list[str]
    variants: list[NameFormResponse]
    regions: list[str]
    article_count: int
    mention_count: int
    first_published_at: datetime | None
    last_published_at: datetime | None
    events: list[EventCountResponse]
    name_source_label: str
    gender_label: str | None
    news: DossierNewsResponse | None
    known: DossierKnownResponse
    warnings: list[str]
    charges: list[ChargeResponse]
    timeline: list[TimelineItemResponse]
    timeline_capped: bool
    timeline_limit: int
    publications: list[EvidenceResponse]
    related: list[RelatedPersonResponse]
    # The cut of `related`: a list this long may hold more people.
    related_limit: int
    graph_url: str

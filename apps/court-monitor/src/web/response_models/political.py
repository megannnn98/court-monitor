"""Political results and candidate responses."""

from datetime import date, datetime

from pydantic import BaseModel

from .common import OptionResponse


class KnownResponse(BaseModel):
    """What the operator's base says of a person."""

    level: str
    label: str
    names: list[str]


class PoliticalArticleResponse(BaseModel):
    article: str
    # On the list of political articles: the legacy page writes it bold.
    political: bool


class PublicationLinkResponse(BaseModel):
    title: str
    url: str
    source: str


class PoliticalRowResponse(BaseModel):
    key: str
    # Surname first for a person, as told for an unnamed figurant.
    name: str
    # The dossier, or the figurant's sentences on «Безымянные» (legacy pages).
    url: str
    unnamed: bool
    done: bool
    news_kind: str | None
    news_label: str | None
    news_reason: str
    # None while the operator's base is not loaded: then nobody is «not in the base».
    known: KnownResponse | None
    not_in_base: bool
    regions: str
    rf_level: str | None
    rf_label: str | None
    rf_entry: str
    rf_included: str | None
    listing: str | None
    awaited: bool
    articles: list[PoliticalArticleResponse]
    basis: str
    basis_quote: str
    memorial: str | None
    first_published_at: datetime | None
    last_published_at: datetime | None
    links: list[PublicationLinkResponse]


class PoliticalListResponse(BaseModel):
    """«Результат» under the filters: one page of rows and every count the filters show."""

    items: list[PoliticalRowResponse]
    total: int
    page: int
    page_size: int
    done_total: int
    awaited: int
    base_loaded: bool
    # The legacy Excel file of exactly these filters, every row, built on the server.
    export_url: str
    periods: list[OptionResponse]
    # Asked with a `queue`: every queue with its count in the period; else empty.
    queues: list[OptionResponse]
    news: list[OptionResponse]
    known: list[OptionResponse]
    who: list[OptionResponse]
    rfm: list[OptionResponse]


class DoneRequest(BaseModel):
    """«обработано» on a person of «Результат»: ticked or not."""

    key: str
    done: bool


class DoneResponse(BaseModel):
    key: str
    done: bool


class CandidateRowResponse(BaseModel):
    """A row of the candidates' table, as the legacy page and its Excel file have it."""

    person_id: int
    # Surname first.
    name: str
    # The Moscow day of the news the row is about.
    news_day: date | None
    category: str | None
    persecution_confidence: float
    event_count: int
    rosfinmonitoring_status: str
    reasons: list[str]


class CandidateTableResponse(BaseModel):
    """The candidates under the legacy page's filters, in its order; the Excel file of the
    same filters holds every one of `total`."""

    snapshot_id: int
    # The first news day shown; None: every day.
    period_start: date | None
    total: int
    items: list[CandidateRowResponse]
    # The legacy Excel file of exactly these filters, built with them on the server.
    export_url: str

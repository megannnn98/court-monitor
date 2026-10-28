"""People, publications and sentence responses."""

from datetime import datetime

from pydantic import BaseModel, Field

from .common import EventCountResponse, OptionResponse, PersonLinkResponse


# Pydantic models for API responses
class PersonResponse(BaseModel):
    """Person response model."""

    id: int
    canonical_name: str
    normalized_name: str
    matching_key: str
    status: str
    merged_into_id: int | None = None


class PersonAliasResponse(BaseModel):
    """Person alias response model."""

    id: int
    person_id: int
    surface_text: str
    normalized_text: str
    matching_key: str
    origin: str
    confidence: float


class PersecutionClassificationResponse(BaseModel):
    """Persecution classification response model."""

    id: int
    person_id: int
    status: str
    confidence: float
    reasons: list[str]
    evidence_types: list[str]
    classifier_name: str
    classifier_version: str


class CandidateResponse(BaseModel):
    """Candidate response model."""

    person_id: int
    canonical_name: str
    normalized_name: str
    persecution_status: str
    persecution_confidence: float
    persecution_reasons: list[str]
    rosfinmonitoring_status: str
    rosfinmonitoring_match_confidence: float | None = None
    event_count: int = 0
    alias_count: int = 0


class ArticleResponse(BaseModel):
    id: int
    source_name: str
    external_id: str
    title: str
    published_at: str | None
    url: str
    text: str


class EvidenceSpanResponse(BaseModel):
    article_id: int
    source_name: str
    title: str
    url: str
    start_offset: int
    end_offset: int
    text: str


class PersonEventResponse(BaseModel):
    id: int
    event_type: str
    event_date: str | None
    role: str
    confidence: float
    evidence: EvidenceSpanResponse


class LatestRosfinMatchResponse(BaseModel):
    snapshot_id: int
    status: str
    confidence: float
    matched_entry_id: int | None
    matched_entry_name: str | None
    reasons: list[str]


class PersonDetailResponse(BaseModel):
    person: PersonResponse
    aliases: list[PersonAliasResponse]
    persecution: PersecutionClassificationResponse | None
    rosfinmonitoring: LatestRosfinMatchResponse | None
    events: list[PersonEventResponse]


class EntityArticleResponse(BaseModel):
    """A Criminal Code article of a person; `shared` when every event naming it accuses
    other people too."""

    article: str
    shared: bool


class EntityRowResponse(BaseModel):
    id: int
    key: str
    # Surname first, as the legacy list writes it.
    name: str
    # «ИИ» or «исправлено» when a model gave the name or a person corrected it.
    name_source_label: str | None
    rf_level: str | None
    rf_label: str | None
    role: str | None
    role_label: str | None
    verdict: str | None
    verdict_label: str | None
    regions: list[str]
    articles: list[EntityArticleResponse]
    events: list[EventCountResponse]
    mention_count: int
    article_count: int
    last_published_at: datetime | None
    variants: list[str]
    dossier_url: str


class EntityListResponse(BaseModel):
    """One page of «Все люди» under the filters, with the counts the legacy page shows."""

    items: list[EntityRowResponse]
    total: int
    page: int
    page_size: int
    hidden_in_list: int
    hidden_maybe_listed: int
    roles_known: bool
    roles: list[OptionResponse]
    verdicts: list[OptionResponse]
    regions: list[str]


class RemovalRequest(BaseModel):
    """«Такого человека нет» on an entity, or that word taken back."""

    key: str = Field(max_length=255)
    removed: bool


class RemovalResponse(BaseModel):
    key: str
    # Surname first; the name the entity had when removed.
    name: str
    removed: bool


class RemovalListResponse(BaseModel):
    """The entities a person removed, the latest first."""

    items: list[RemovalResponse]


class NameCorrectionRequest(BaseModel):
    """A person's correction of an entity's name, kept for every rebuild."""

    key: str = Field(max_length=255)
    name: str = Field(max_length=200)


class NameCorrectionResponse(BaseModel):
    key: str
    # The name as kept, cleaned.
    name: str


class OfficialMarkRequest(BaseModel):
    """A person's word: the entity is (or is not) an official."""

    key: str = Field(max_length=255)
    official: bool


class OfficialMarkResponse(BaseModel):
    key: str
    official: bool


class PublicationRowResponse(BaseModel):
    id: int
    title: str
    published_at: datetime | None
    source: str
    url: str
    people: list[PersonLinkResponse]
    # People beyond the ones shown.
    more_people: int
    events: list[EventCountResponse]


class SourceCountResponse(BaseModel):
    id: int
    name: str
    count: int


class PublicationListResponse(BaseModel):
    items: list[PublicationRowResponse]
    total: int
    page: int
    page_size: int
    sources: list[SourceCountResponse]


class ArticleMentionsResponse(BaseModel):
    """The people an article names, most mentioned first, and the events found in it."""

    people: list[PersonLinkResponse]
    events: list[EventCountResponse]


class SentenceRowResponse(BaseModel):
    """One case of «Приговоры» (several rows of one sentence told by several sources), or
    one row a person took out of the counts."""

    row_ids: list[int]
    person: str
    region: str | None
    kind_label: str
    in_absentia: bool
    # «2 г. 6 мес.», «штраф 35 000 ₽» or a dash, as the legacy page writes it.
    term: str
    sentenced_on: str | None
    reason_label: str
    reason_text: str
    article_id: int
    title: str
    # The source the case is shown by; None for a hidden row.
    source: str | None
    more_publications: int
    quote: str


class SentenceListResponse(BaseModel):
    """«Приговоры» under the filters, or (`view=hidden`) what was taken out."""

    view: str
    reason: str
    region: str
    items: list[SentenceRowResponse]
    total: int
    page: int
    page_size: int
    read: int
    cases: int
    hidden: int
    reasons: list[OptionResponse]
    regions: list[str]

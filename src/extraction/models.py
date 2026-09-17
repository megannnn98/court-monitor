from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class EntityType(StrEnum):
    PERSON = "person"
    ORGANIZATION = "organization"
    COURT = "court"
    LOCATION = "location"
    LEGAL_REFERENCE = "legal_reference"


class EventType(StrEnum):
    CASE_OPENED = "case_opened"
    SEARCH = "search"
    DETENTION = "detention"
    ARREST = "arrest"
    CHARGE = "charge"
    SENTENCE = "sentence"
    FINE = "fine"
    RELEASE = "release"
    OTHER = "other"


class EventEntityRole(StrEnum):
    SUBJECT = "subject"
    TARGET = "target"
    COURT = "court"
    AUTHORITY = "authority"
    LOCATION = "location"
    LEGAL_BASIS = "legal_basis"


class ExtractionRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# Sources whose document title is one person's full name in the nominative, surname first
# (a registry card): the name is taken from the title, never searched for or declined.
MEMOPZK_REGISTRY_SOURCE_NAME = "Поддержка политзаключённых. Мемориал: реестр"
NAMED_TITLE_SOURCES = frozenset({MEMOPZK_REGISTRY_SOURCE_NAME})


class ExtractionDocument(BaseModel):
    article_id: int
    title: str
    text: str
    published_at: datetime | None
    source_name: str
    source_url: str
    content_hash: str


class RawMention(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    entity_type: EntityType
    surface_text: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    extractor_name: str = "rule-based"
    extractor_version: str = "1.0.0"

    @field_validator("surface_text")
    @classmethod
    def validate_surface_text(cls, value: str) -> str:
        if not value:
            raise ValueError("surface_text must not be empty")
        return value

    @model_validator(mode="after")
    def validate_offsets(self) -> RawMention:
        if self.start_offset >= self.end_offset:
            raise ValueError("start_offset must be less than end_offset")
        return self


class PersonNormalizedData(BaseModel):
    full_name: str
    last_name: str | None = None
    first_name: str | None = None
    patronymic: str | None = None
    matching_key: str


class LegalReferenceNormalizedData(BaseModel):
    code: str
    article: str | None = None
    part: str | None = None
    clause: str | None = None


class OrganizationNormalizedData(BaseModel):
    name: str
    organization_type: str
    location: str | None = None
    matching_key: str


class LocationNormalizedData(BaseModel):
    name: str
    location_type: str | None = None
    matching_key: str


NormalizedData = Annotated[
    PersonNormalizedData
    | LegalReferenceNormalizedData
    | OrganizationNormalizedData
    | LocationNormalizedData,
    Field(union_mode="left_to_right"),
]


class NormalizedMention(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    entity_type: EntityType
    surface_text: str
    normalized_text: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    normalized_data: NormalizedData
    extractor_name: str
    extractor_version: str
    normalizer_version: str

    @field_validator("surface_text", "normalized_text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value:
            raise ValueError("text fields must not be empty")
        return value

    @model_validator(mode="after")
    def validate_offsets(self) -> NormalizedMention:
        if self.start_offset >= self.end_offset:
            raise ValueError("start_offset must be less than end_offset")
        return self


class EventEntityLink(BaseModel):
    role: EventEntityRole
    mention_index: int = Field(ge=0)


class EventMention(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    event_type: EventType
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    event_date: datetime | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    links: list[EventEntityLink] = Field(default_factory=list)
    extractor_name: str = "rule-based-events"
    extractor_version: str = "1.0.0"

    @model_validator(mode="after")
    def validate_offsets(self) -> EventMention:
        if self.start_offset >= self.end_offset:
            raise ValueError("start_offset must be less than end_offset")
        return self


class ArticleExtractionResult(BaseModel):
    document: ExtractionDocument
    mentions: list[NormalizedMention]
    events: list[EventMention]
    extractor_name: str
    extractor_version: str
    normalizer_version: str


class ExtractionSaveResult(BaseModel):
    run_id: int
    article_id: int
    status: ExtractionRunStatus
    mentions_created: int = 0
    events_created: int = 0
    skipped_existing: bool = False
    error_message: str | None = None


class BatchExtractionResult(BaseModel):
    articles_processed: int = 0
    articles_skipped: int = 0
    articles_failed: int = 0
    mentions_created: int = 0
    events_created: int = 0
    failures: list[str] = Field(default_factory=list)


class EntityExtractor(Protocol):
    extractor_name: str
    extractor_version: str

    def extract(self, document: ExtractionDocument) -> list[RawMention]: ...


class MentionNormalizer(Protocol):
    normalizer_version: str

    def supports(self, entity_type: EntityType) -> bool: ...

    def normalize(
        self,
        mention: RawMention,
        document: ExtractionDocument,
    ) -> NormalizedMention: ...


class ExtractionPersistence(Protocol):
    def save(self, result: ArticleExtractionResult) -> ExtractionSaveResult: ...

    def save_failed(
        self,
        document: ExtractionDocument,
        *,
        extractor_name: str,
        extractor_version: str,
        normalizer_version: str,
        error_message: str,
    ) -> ExtractionSaveResult: ...

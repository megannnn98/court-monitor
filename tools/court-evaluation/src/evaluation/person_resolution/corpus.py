"""The ER evaluation corpus as the pipeline stores it: persons, aliases, classifications,
events with short event texts, seeded into a disposable database."""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session, sessionmaker

from analysis import testing as analysis_testing
from ingestion import testing as ingestion_testing
from persons import testing as persons_testing
from processing import testing as processing_testing

CORPUS_FETCHED_AT = datetime(2026, 1, 1, tzinfo=UTC)
_ENTITY_ROLES = ("court", "location", "legal_basis")


class CorpusClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    evidence_types: list[str] = Field(default_factory=list)


class CorpusEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    event_type: str
    event_date: date | None = None
    source: str
    text: str = Field(min_length=1)
    court: str | None = None
    location: str | None = None
    legal_basis: str | None = None

    @model_validator(mode="after")
    def validate_entities_are_in_text(self) -> CorpusEvent:
        for role in _ENTITY_ROLES:
            value = getattr(self, role)
            if value is not None and value not in self.text:
                raise ValueError(f"event {self.key}: {role} {value!r} is not part of its text")
        return self


class CorpusPerson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    classification: CorpusClassification | None = None
    events: list[CorpusEvent] = Field(default_factory=list)


class ErEvaluationCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persons: list[CorpusPerson]

    @model_validator(mode="after")
    def validate_unique_keys(self) -> ErEvaluationCorpus:
        keys = [person.key for person in self.persons] + [
            event.key for person in self.persons for event in person.events
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("corpus keys must be unique")
        return self


class CorpusIds(BaseModel):
    persons: dict[str, int]
    events: dict[str, int]


def seed_corpus(session_factory: sessionmaker[Session], corpus: ErEvaluationCorpus) -> CorpusIds:
    """Insert the corpus as persons/articles/events/classifications. Needs empty tables."""
    person_ids: dict[str, int] = {}
    event_ids: dict[str, int] = {}
    with session_factory() as session:
        sources: dict[str, int] = {}

        def source_for(name: str) -> int:
            if name not in sources:
                sources[name] = ingestion_testing.source_named(
                    session, name, f"https://evaluation.test/{len(sources)}"
                )
            return sources[name]

        for person in corpus.persons:
            person_id = persons_testing.add_person(
                session,
                canonical_name=person.canonical_name,
                normalized_name=person.canonical_name.lower(),
                matching_key=f"eval|{person.key}",
                aliases=[
                    (alias, alias.lower(), f"eval|{person.key}|{alias}") for alias in person.aliases
                ],
            )
            person_ids[person.key] = person_id
            if person.classification is not None:
                analysis_testing.add_classification(
                    session,
                    person_id,
                    status=person.classification.status,
                    confidence=person.classification.confidence,
                    reasons=person.classification.reasons,
                    evidence_types=person.classification.evidence_types,
                    classifier="evaluation-corpus",
                    at=CORPUS_FETCHED_AT,
                )
            for event in person.events:
                event_ids[event.key] = _seed_event(
                    session, source_for(event.source), person_id, event
                )
        session.commit()
    return CorpusIds(persons=person_ids, events=event_ids)


def _seed_event(session: Session, source_id: int, person_id: int, event: CorpusEvent) -> int:
    article_id = ingestion_testing.add_article(
        session,
        source_id,
        external_id=f"eval-{event.key}",
        url=f"https://evaluation.test/articles/{event.key}",
        fetched_at=CORPUS_FETCHED_AT,
        title=event.key,
        text=event.text,
    )
    event_id = processing_testing.add_extracted_event(
        session,
        article_id,
        content_hash=f"eval-{event.key}",
        extractor="evaluation-corpus",
        at=CORPUS_FETCHED_AT,
        event_type=event.event_type,
        event_date=(
            datetime.combine(event.event_date, datetime.min.time(), UTC)
            if event.event_date
            else None
        ),
        end_offset=len(event.text),
        mentions=[
            processing_testing.FixtureMention(
                "legal_reference" if role == "legal_basis" else role,
                value,
                event.text.index(value),
                role,
            )
            for role in _ENTITY_ROLES
            if (value := getattr(event, role)) is not None
        ],
    )
    persons_testing.link_event(session, person_id, event_id, role="subject")
    return event_id

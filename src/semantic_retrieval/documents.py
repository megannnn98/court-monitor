"""Deterministic semantic documents for canonical Person and Event entities.

The text is built only from structured data stored in PostgreSQL and linked to
the entity: names/aliases, the latest persecution classification, linked
events with their own extracted span and linked court/location/legal
references, and the sentences where the person is mentioned (how the source
describes them: «антифашист», «нацбол из Ярославля»). Never a whole article,
never LLM output. Every list is sorted, so
the same entity state always yields the same text and content hash.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    EventEntityMentionRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
    PersecutionClassificationRecord,
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonRecord,
    Source,
    SourceDocument,
)
from persecution.queries import latest_persecution_classification_ids
from persons.models import PersonStatus
from semantic_retrieval.models import RetrievalEntityType, SemanticDocument

# Bump when the text format changes: every document is then re-embedded.
# 2: sentences mentioning the person.
PERSON_REPRESENTATION_VERSION = 2
EVENT_REPRESENTATION_VERSION = 1

# Extracted spans are sentences; cap them so a bad extraction offset can
# never pull a large part of an article into the representation.
MAX_SPAN_CHARS = 400
# Mention sentences per person, in article order; the window bounds the sentence search.
MAX_MENTION_SENTENCES = 3
_MENTION_WINDOW_CHARS = MAX_SPAN_CHARS
_SENTENCE_END = re.compile(r"[.!?](?=\s)|\n")

EVENT_TYPE_LABELS = {
    "case_opened": "возбуждение дела",
    "search": "обыск",
    "detention": "задержание",
    "arrest": "арест",
    "charge": "обвинение",
    "sentence": "приговор",
    "fine": "штраф",
    "release": "освобождение",
    "other": "другое событие",
}

PERSECUTION_STATUS_LABELS = {
    "political": "политическое",
    "non_political": "неполитическое",
    "uncertain": "не установлено",
    "needs_review": "требует проверки",
}

EVIDENCE_TYPE_LABELS = {
    "political_article": "политическая статья",
    "political_event": "политическое событие",
    "political_charge": "политическое обвинение",
    "human_rights_defender": "правозащитная деятельность",
    "journalist": "журналистская деятельность",
    "activist": "активизм",
    "dissenting_opinion": "несогласие с властью",
    "religious_persecution": "религиозное преследование",
    "anti_war_activity": "антивоенная деятельность",
    "lgbt_persecution": "преследование ЛГБТ",
}

# Non-person event roles rendered as context, in this order.
ENTITY_ROLE_LABELS = {
    "court": "Суд",
    "authority": "Орган",
    "location": "Место",
    "legal_basis": "Правовое основание",
}


def compute_content_hash(entity_type: RetrievalEntityType, version: int, text: str) -> str:
    return hashlib.sha256(f"{entity_type.value}\n{version}\n{text}".encode()).hexdigest()


class SemanticDocumentBuilder(Protocol):
    entity_type: RetrievalEntityType

    def list_entity_ids(self, *, limit: int | None = None) -> list[int]:
        """Ids of every indexable entity, ascending."""
        ...

    def build(self, entity_ids: Sequence[int]) -> list[SemanticDocument]:
        """Documents for existing indexable entities, ordered by id; others are skipped."""
        ...


@dataclass
class _EventContext:
    event_id: int
    event_type: str
    event_date: datetime | None
    span: str
    created_at: datetime
    source_name: str | None = None
    participants: list[tuple[str, str]] = field(default_factory=list)
    entities: dict[str, list[str]] = field(default_factory=dict)


def _clean(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > MAX_SPAN_CHARS:
        collapsed = collapsed[:MAX_SPAN_CHARS].rstrip() + "…"
    return collapsed.rstrip(".")


def _mention_sentence(window: str, start: int, end: int) -> str:
    """The sentence of `window` that contains the mention span [start, end)."""
    # A sentence ends at a mark followed by whitespace: «статье 207.3 УК» is one sentence.
    boundaries = [match.end() for match in _SENTENCE_END.finditer(window)]
    left = max((index for index in boundaries if index <= start), default=0)
    right = min((index for index in boundaries if index > end), default=len(window))
    return _clean(window[left:right])


def _load_mention_sentences(session: Session, person_ids: Sequence[int]) -> dict[int, list[str]]:
    if not person_ids:
        return {}
    window_start = func.greatest(EntityMentionRecord.start_offset - _MENTION_WINDOW_CHARS, 0)
    rows = session.execute(
        select(
            EntityMentionRecord.person_id,
            EntityMentionRecord.start_offset,
            EntityMentionRecord.end_offset,
            window_start,
            func.substr(
                ParsedArticleRecord.text,
                window_start + 1,
                EntityMentionRecord.end_offset - window_start + _MENTION_WINDOW_CHARS,
            ),
        )
        .join(
            ArticleExtractionRunRecord,
            ArticleExtractionRunRecord.id == EntityMentionRecord.extraction_run_id,
        )
        .join(ParsedArticleRecord, ParsedArticleRecord.id == ArticleExtractionRunRecord.article_id)
        .where(EntityMentionRecord.person_id.in_(person_ids))
        .order_by(EntityMentionRecord.person_id, EntityMentionRecord.id)
    ).all()
    sentences: dict[int, list[str]] = defaultdict(list)
    for person_id, start, end, offset, window in rows:
        if person_id is None or len(sentences[person_id]) >= MAX_MENTION_SENTENCES:
            continue
        sentence = _mention_sentence(window or "", start - offset, end - offset)
        if sentence and sentence not in sentences[person_id]:
            sentences[person_id].append(sentence)
    return sentences


def _event_label(context: _EventContext) -> str:
    label = EVENT_TYPE_LABELS.get(context.event_type, context.event_type)
    date = context.event_date.date().isoformat() if context.event_date else "дата неизвестна"
    return f"{label}, {date}"


def _entity_line(context: _EventContext) -> str:
    parts = [
        f"{label}: {'; '.join(context.entities[role])}."
        for role, label in ENTITY_ROLE_LABELS.items()
        if context.entities.get(role)
    ]
    return " ".join(parts)


def _load_event_contexts(session: Session, event_ids: Sequence[int]) -> dict[int, _EventContext]:
    if not event_ids:
        return {}
    rows = session.execute(
        select(
            ExtractedEventRecord,
            func.substr(
                ParsedArticleRecord.text,
                ExtractedEventRecord.start_offset + 1,
                ExtractedEventRecord.end_offset - ExtractedEventRecord.start_offset,
            ),
            Source.name,
        )
        .join(
            ArticleExtractionRunRecord,
            ArticleExtractionRunRecord.id == ExtractedEventRecord.extraction_run_id,
        )
        .join(ParsedArticleRecord, ParsedArticleRecord.id == ArticleExtractionRunRecord.article_id)
        .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
        .join(Source, Source.id == SourceDocument.source_id)
        .where(ExtractedEventRecord.id.in_(event_ids))
    ).all()
    contexts = {
        event.id: _EventContext(
            event_id=event.id,
            event_type=event.event_type,
            event_date=event.event_date,
            span=_clean(span or ""),
            created_at=event.created_at,
            source_name=source_name,
        )
        for event, span, source_name in rows
    }

    for event_id, role, surface_text in session.execute(
        select(
            EventEntityMentionRecord.event_id,
            EventEntityMentionRecord.role,
            EntityMentionRecord.surface_text,
        )
        .join(EntityMentionRecord, EntityMentionRecord.id == EventEntityMentionRecord.mention_id)
        .where(EventEntityMentionRecord.event_id.in_(event_ids))
    ).all():
        if role in ENTITY_ROLE_LABELS and event_id in contexts:
            values = contexts[event_id].entities.setdefault(role, [])
            if surface_text not in values:
                values.append(surface_text)

    for event_id, role, canonical_name in session.execute(
        select(
            PersonEventLinkRecord.event_id, PersonEventLinkRecord.role, PersonRecord.canonical_name
        )
        .join(PersonRecord, PersonRecord.id == PersonEventLinkRecord.person_id)
        .where(
            PersonEventLinkRecord.event_id.in_(event_ids),
            PersonRecord.status == PersonStatus.ACTIVE.value,
        )
    ).all():
        if event_id in contexts:
            contexts[event_id].participants.append((canonical_name, role))

    for context in contexts.values():
        context.participants.sort()
        for values in context.entities.values():
            values.sort()
    return contexts


class PersonSemanticDocumentBuilder:
    entity_type = RetrievalEntityType.PERSON

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_entity_ids(self, *, limit: int | None = None) -> list[int]:
        query = (
            select(PersonRecord.id)
            .where(PersonRecord.status == PersonStatus.ACTIVE.value)
            .order_by(PersonRecord.id)
            .limit(limit)
        )
        with self._session_factory() as session:
            return list(session.scalars(query).all())

    def build(self, entity_ids: Sequence[int]) -> list[SemanticDocument]:
        if not entity_ids:
            return []
        with self._session_factory() as session:
            persons = session.scalars(
                select(PersonRecord)
                .where(
                    PersonRecord.id.in_(entity_ids),
                    PersonRecord.status == PersonStatus.ACTIVE.value,
                )
                .order_by(PersonRecord.id)
            ).all()
            person_ids = [person.id for person in persons]

            aliases: dict[int, set[str]] = defaultdict(set)
            for person_id, surface_text in session.execute(
                select(PersonAliasRecord.person_id, PersonAliasRecord.surface_text).where(
                    PersonAliasRecord.person_id.in_(person_ids)
                )
            ).all():
                aliases[person_id].add(surface_text)

            classifications = {
                record.person_id: record
                for record in session.scalars(
                    select(PersecutionClassificationRecord).where(
                        PersecutionClassificationRecord.person_id.in_(person_ids),
                        PersecutionClassificationRecord.id.in_(
                            latest_persecution_classification_ids()
                        ),
                    )
                ).all()
            }

            events_by_person: dict[int, set[int]] = defaultdict(set)
            for person_id, event_id in session.execute(
                select(PersonEventLinkRecord.person_id, PersonEventLinkRecord.event_id).where(
                    PersonEventLinkRecord.person_id.in_(person_ids)
                )
            ).all():
                events_by_person[person_id].add(event_id)
            contexts = _load_event_contexts(
                session, sorted(set().union(*events_by_person.values()))
            )
            mention_sentences = _load_mention_sentences(session, person_ids)

        documents: list[SemanticDocument] = []
        for person in persons:
            lines = [f"Персона: {person.canonical_name}."]
            other_names = sorted(aliases[person.id] - {person.canonical_name})
            if other_names:
                lines.append(f"Другие написания: {'; '.join(other_names)}.")

            classification = classifications.get(person.id)
            if classification is not None:
                status_label = PERSECUTION_STATUS_LABELS.get(
                    classification.status, classification.status
                )
                parts = [f"Классификация преследования: {status_label}."]
                if classification.reasons:
                    parts.append(f"Основания: {'; '.join(classification.reasons)}.")
                if classification.evidence_types:
                    labels = [
                        EVIDENCE_TYPE_LABELS.get(value, value)
                        for value in classification.evidence_types
                    ]
                    parts.append(f"Признаки: {'; '.join(labels)}.")
                lines.append(" ".join(parts))

            if mention_sentences.get(person.id):
                lines.append("Упоминания:")
                lines.extend(f"- {sentence}." for sentence in mention_sentences[person.id])

            person_events = sorted(
                (
                    contexts[event_id]
                    for event_id in events_by_person[person.id]
                    if event_id in contexts
                ),
                key=lambda context: (
                    context.event_date is None,
                    context.event_date.timestamp() if context.event_date else 0.0,
                    context.event_id,
                ),
            )
            if person_events:
                lines.append("События:")
                for context in person_events:
                    entity_line = _entity_line(context)
                    lines.append(
                        f"- {_event_label(context)}: {context.span}."
                        + (f" {entity_line}" if entity_line else "")
                    )

            text = "\n".join(lines)
            documents.append(
                SemanticDocument(
                    entity_type=self.entity_type,
                    entity_id=person.id,
                    text=text,
                    representation_version=PERSON_REPRESENTATION_VERSION,
                    content_hash=compute_content_hash(
                        self.entity_type, PERSON_REPRESENTATION_VERSION, text
                    ),
                    source_updated_at=person.updated_at or person.created_at,
                )
            )
        return documents


class EventSemanticDocumentBuilder:
    entity_type = RetrievalEntityType.EVENT

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_entity_ids(self, *, limit: int | None = None) -> list[int]:
        query = select(ExtractedEventRecord.id).order_by(ExtractedEventRecord.id).limit(limit)
        with self._session_factory() as session:
            return list(session.scalars(query).all())

    def build(self, entity_ids: Sequence[int]) -> list[SemanticDocument]:
        with self._session_factory() as session:
            contexts = _load_event_contexts(session, sorted(set(entity_ids)))

        documents: list[SemanticDocument] = []
        for event_id in sorted(contexts):
            context = contexts[event_id]
            lines = [f"Событие: {_event_label(context)}.", f"Фрагмент: {context.span}."]
            if context.participants:
                participants = "; ".join(f"{name} ({role})" for name, role in context.participants)
                lines.append(f"Участники: {participants}.")
            entity_line = _entity_line(context)
            if entity_line:
                lines.append(entity_line)
            if context.source_name:
                lines.append(f"Источник: {context.source_name}.")

            text = "\n".join(lines)
            documents.append(
                SemanticDocument(
                    entity_type=self.entity_type,
                    entity_id=event_id,
                    text=text,
                    representation_version=EVENT_REPRESENTATION_VERSION,
                    content_hash=compute_content_hash(
                        self.entity_type, EVENT_REPRESENTATION_VERSION, text
                    ),
                    source_updated_at=context.created_at,
                )
            )
        return documents

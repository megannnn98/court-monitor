"""Entity-level retrieval evaluation: corpus loading, cases, per-backend metrics.

The corpus is synthetic structured data (persons, classifications, events
with short event texts) loaded into a disposable database, so every backend
retrieves over real semantic documents built by the production builders.
Judgments reference corpus keys, never database ids.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from sqlalchemy.orm import Session, sessionmaker

from orm_models import (
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
from semantic_retrieval.metrics import ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank
from semantic_retrieval.models import (
    RetrievalBackend,
    RetrievalEntityType,
    RetrievalQuery,
)
from semantic_retrieval.retrievers import EntityRetriever

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


class EntityRetrievalCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persons: list[CorpusPerson]

    @model_validator(mode="after")
    def validate_unique_keys(self) -> EntityRetrievalCorpus:
        keys = [person.key for person in self.persons] + [
            event.key for person in self.persons for event in person.events
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("corpus keys must be unique")
        return self


class EntityRetrievalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    query_id: str
    query_text: str
    entity_type: RetrievalEntityType
    # Graded relevance by corpus key: 2 highly relevant, 1 relevant, 0 not.
    judgments: dict[str, int]
    # No lexical (stemmed) overlap between the query and relevant documents.
    semantic_only: bool = False

    @model_validator(mode="after")
    def validate_grades(self) -> EntityRetrievalCase:
        if not any(grade > 0 for grade in self.judgments.values()):
            raise ValueError(f"case {self.query_id} has no relevant entity")
        if any(grade not in (0, 1, 2) for grade in self.judgments.values()):
            raise ValueError(f"case {self.query_id}: grades must be 0, 1 or 2")
        return self


def load_corpus(path: Path) -> EntityRetrievalCorpus:
    return EntityRetrievalCorpus.model_validate_json(path.read_bytes())


def load_cases(path: Path) -> list[EntityRetrievalCase]:
    return TypeAdapter(list[EntityRetrievalCase]).validate_json(path.read_bytes())


class CorpusIds(BaseModel):
    persons: dict[str, int]
    events: dict[str, int]

    def ids_for(self, entity_type: RetrievalEntityType) -> dict[str, int]:
        return self.persons if entity_type is RetrievalEntityType.PERSON else self.events


def seed_corpus(session_factory: sessionmaker[Session], corpus: EntityRetrievalCorpus) -> CorpusIds:
    """Insert the corpus as persons/articles/events/classifications. Needs empty tables."""
    person_ids: dict[str, int] = {}
    event_ids: dict[str, int] = {}
    with session_factory() as session:
        sources: dict[str, Source] = {}

        def source_for(name: str) -> Source:
            if name not in sources:
                source = Source(name=name, base_url=f"https://evaluation.test/{len(sources)}")
                session.add(source)
                session.flush()
                sources[name] = source
            return sources[name]

        for person in corpus.persons:
            record = PersonRecord(
                canonical_name=person.canonical_name,
                normalized_name=person.canonical_name.lower(),
                matching_key=f"eval|{person.key}",
                status="active",
            )
            session.add(record)
            session.flush()
            person_ids[person.key] = record.id
            for alias in person.aliases:
                session.add(
                    PersonAliasRecord(
                        person_id=record.id,
                        surface_text=alias,
                        normalized_text=alias.lower(),
                        matching_key=f"eval|{person.key}|{alias}",
                        origin="manual",
                        confidence=1.0,
                    )
                )
            if person.classification is not None:
                session.add(
                    PersecutionClassificationRecord(
                        person_id=record.id,
                        status=person.classification.status,
                        confidence=person.classification.confidence,
                        reasons=person.classification.reasons,
                        evidence_types=person.classification.evidence_types,
                        classifier_name="evaluation-corpus",
                        classifier_version="1",
                        classified_at=CORPUS_FETCHED_AT,
                    )
                )
            for event in person.events:
                event_ids[event.key] = _seed_event(
                    session, source_for(event.source), record.id, event
                )
        session.commit()
    return CorpusIds(persons=person_ids, events=event_ids)


def _seed_event(session: Session, source: Source, person_id: int, event: CorpusEvent) -> int:
    document = SourceDocument(
        source_id=source.id,
        external_id=f"eval-{event.key}",
        canonical_url=f"https://evaluation.test/articles/{event.key}",
        fetched_at=CORPUS_FETCHED_AT,
        content_type="text/plain",
        raw_content=event.text.encode(),
    )
    session.add(document)
    session.flush()
    article = ParsedArticleRecord(
        document_id=document.id, title=event.key, published_at=None, text=event.text
    )
    session.add(article)
    session.flush()
    run = ArticleExtractionRunRecord(
        article_id=article.id,
        article_content_hash=f"eval-{event.key}",
        extractor_name="evaluation-corpus",
        extractor_version="1",
        normalizer_version="1",
        status="succeeded",
        started_at=CORPUS_FETCHED_AT,
        finished_at=CORPUS_FETCHED_AT,
    )
    session.add(run)
    session.flush()
    extracted = ExtractedEventRecord(
        extraction_run_id=run.id,
        event_type=event.event_type,
        event_date=(
            datetime.combine(event.event_date, datetime.min.time(), UTC)
            if event.event_date
            else None
        ),
        start_offset=0,
        end_offset=len(event.text),
        confidence=1.0,
        attributes={},
        extractor_name="evaluation-corpus",
        extractor_version="1",
    )
    session.add(extracted)
    session.flush()
    session.add(
        PersonEventLinkRecord(
            person_id=person_id, event_id=extracted.id, role="subject", confidence=1.0
        )
    )
    for role in _ENTITY_ROLES:
        value = getattr(event, role)
        if value is None:
            continue
        start = event.text.index(value)
        mention = EntityMentionRecord(
            extraction_run_id=run.id,
            entity_type="legal_reference" if role == "legal_basis" else role,
            surface_text=value,
            normalized_text=value.lower(),
            start_offset=start,
            end_offset=start + len(value),
            confidence=1.0,
            normalized_data={},
            extractor_name="evaluation-corpus",
            extractor_version="1",
            normalizer_version="1",
        )
        session.add(mention)
        session.flush()
        session.add(
            EventEntityMentionRecord(event_id=extracted.id, mention_id=mention.id, role=role)
        )
    return extracted.id


class CaseResult(BaseModel):
    query_id: str
    semantic_only: bool
    retrieved: list[str]
    reciprocal_rank: float
    recall_at_k: float
    precision_at_k: float
    ndcg_at_k: float


class MetricSummary(BaseModel):
    cases: int
    mrr: float
    recall_at_k: float
    precision_at_k: float
    ndcg_at_k: float


class BackendEvaluation(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    backend: RetrievalBackend
    k: int
    overall: MetricSummary
    semantic_only: MetricSummary
    results: list[CaseResult]


def _summary(results: Sequence[CaseResult]) -> MetricSummary:
    count = len(results)

    def mean(values: Sequence[float]) -> float:
        return sum(values) / count if count else 0.0

    return MetricSummary(
        cases=count,
        mrr=mean([r.reciprocal_rank for r in results]),
        recall_at_k=mean([r.recall_at_k for r in results]),
        precision_at_k=mean([r.precision_at_k for r in results]),
        ndcg_at_k=mean([r.ndcg_at_k for r in results]),
    )


def evaluate_backend(
    *,
    backend: RetrievalBackend,
    retriever: EntityRetriever,
    cases: Sequence[EntityRetrievalCase],
    ids: CorpusIds,
    k: int = 5,
) -> BackendEvaluation:
    if k <= 0:
        raise ValueError("k must be greater than 0")
    results: list[CaseResult] = []
    for case in cases:
        key_by_id = {entity_id: key for key, entity_id in ids.ids_for(case.entity_type).items()}
        judgments: Mapping[int, int] = {
            ids.ids_for(case.entity_type)[key]: grade for key, grade in case.judgments.items()
        }
        retrieved = retriever.retrieve(
            RetrievalQuery(text=case.query_text, entity_type=case.entity_type, limit=max(k, 20))
        ).entity_ids
        results.append(
            CaseResult(
                query_id=case.query_id,
                semantic_only=case.semantic_only,
                retrieved=[
                    key_by_id.get(entity_id, f"#{entity_id}") for entity_id in retrieved[:k]
                ],
                reciprocal_rank=reciprocal_rank(retrieved, judgments),
                recall_at_k=recall_at_k(retrieved, judgments, k=k),
                precision_at_k=precision_at_k(retrieved, judgments, k=k),
                ndcg_at_k=ndcg_at_k(retrieved, judgments, k=k),
            )
        )
    return BackendEvaluation(
        backend=backend,
        k=k,
        overall=_summary(results),
        semantic_only=_summary([r for r in results if r.semantic_only]),
        results=results,
    )


def format_comparison(evaluations: Sequence[BackendEvaluation]) -> str:
    """Markdown table: overall and semantic-only metrics per backend."""
    if not evaluations:
        return ""
    k = evaluations[0].k
    lines = [
        (
            f"| backend | MRR | Recall@{k} | nDCG@{k} | P@{k} | MRR (semantic-only) | "
            f"Recall@{k} (semantic-only) | nDCG@{k} (semantic-only) |"
        ),
        "|---|---|---|---|---|---|---|---|",
    ]
    for evaluation in evaluations:
        o, s = evaluation.overall, evaluation.semantic_only
        lines.append(
            f"| {evaluation.backend.value} | {o.mrr:.3f} | {o.recall_at_k:.3f} | "
            f"{o.ndcg_at_k:.3f} | {o.precision_at_k:.3f} | {s.mrr:.3f} | {s.recall_at_k:.3f} | "
            f"{s.ndcg_at_k:.3f} |"
        )
    return "\n".join(lines)

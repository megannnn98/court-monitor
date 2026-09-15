"""Semantic retrieval benchmark on the real corpus.

Queries judge golden persons (and golden events) by stable ids; they are
mapped to the canonical persons and extracted events of the evaluation run.
Backends are reported separately; retrieval scores are never domain
confidence. Dense/hybrid need the real embedding model and Qdrant; without
them the section is NOT_RUN with the reason.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from evaluation.real_world.component_evaluation import IdentityMap
from evaluation.real_world.db_state import PipelineState
from evaluation.real_world.golden import GoldenDataset, GoldenSplit
from evaluation.real_world.metrics import Span, match_spans
from evaluation.real_world.models import DEFAULT_DATA_DIR
from evaluation.real_world.results import (
    BackendRetrieval,
    ErrorComponent,
    Failure,
    RetrievalSection,
    SectionStatus,
    Severity,
)
from semantic_retrieval.evaluation import CorpusIds, EntityRetrievalCase, evaluate_backend
from semantic_retrieval.models import RetrievalBackend, RetrievalEntityType
from semantic_retrieval.retrievers import EntityRetriever

DEFAULT_RETRIEVAL_QUERIES_PATH = DEFAULT_DATA_DIR / "retrieval_queries.json"
SEMANTIC_BACKENDS = (RetrievalBackend.DENSE, RetrievalBackend.HYBRID)


class RealRetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: str
    text: str
    entity_type: RetrievalEntityType
    split: GoldenSplit
    # Golden person id (persons) or "case_id/event_id" (events) -> grade 1 or 2.
    judgments: dict[str, int] = Field(min_length=1)
    # No shared word stem between the query and the relevant documents.
    semantic_only: bool = False
    notes: str | None = None


def load_retrieval_queries(path: Path = DEFAULT_RETRIEVAL_QUERIES_PATH) -> list[RealRetrievalQuery]:
    if not path.exists():
        return []
    return TypeAdapter(list[RealRetrievalQuery]).validate_json(path.read_bytes())


def entity_ids(dataset: GoldenDataset, state: PipelineState, identity: IdentityMap) -> CorpusIds:
    persons = {
        golden_id: person_id
        for golden_id in identity.true_person
        if (person_id := identity.mapped_person(golden_id)) is not None
    }
    events: dict[str, int] = {}
    for article in dataset.articles:
        db_events = state.events.get(article.key, [])
        pairs = match_spans(
            [Span(e.evidence.start, e.evidence.end, e.event_type.value) for e in article.events],
            [Span(e.start, e.end, e.event_type) for e in db_events],
        )
        for gold_index, db_index in pairs:
            events[f"{article.case_id}/{article.events[gold_index].event_id}"] = db_events[
                db_index
            ].event_id
    return CorpusIds(persons=persons, events=events)


def evaluate_retrieval(
    *,
    queries: Sequence[RealRetrievalQuery],
    dataset: GoldenDataset,
    state: PipelineState,
    identity: IdentityMap,
    retrievers: Mapping[RetrievalBackend, EntityRetriever],
    embedding_model_id: str | None,
    not_run_reason: str | None,
    failures: list[Failure],
) -> RetrievalSection:
    section = RetrievalSection(
        status=SectionStatus.NOT_RUN,
        queries=len(queries),
        person_queries=sum(q.entity_type is RetrievalEntityType.PERSON for q in queries),
        event_queries=sum(q.entity_type is RetrievalEntityType.EVENT for q in queries),
        embedding_model_id=embedding_model_id,
    )
    if not queries:
        section.not_run_reason = "no retrieval queries"
        return section
    ids = entity_ids(dataset, state, identity)
    cases: list[EntityRetrievalCase] = []
    for query in queries:
        known = ids.ids_for(query.entity_type)
        judgments = {key: grade for key, grade in query.judgments.items() if key in known}
        if not judgments:
            failures.append(
                Failure(
                    component=ErrorComponent.RETRIEVAL,
                    severity=Severity.S2,
                    kind="retrieval_target_not_in_index",
                    detail=f"{query.query_id}: no judged entity exists in the pipeline state",
                )
            )
            continue
        cases.append(
            EntityRetrievalCase(
                query_id=query.query_id,
                query_text=query.text,
                entity_type=query.entity_type,
                judgments=judgments,
                semantic_only=query.semantic_only,
            )
        )
    if not retrievers:
        section.not_run_reason = not_run_reason or "no retriever available"
        return section
    for backend, retriever in retrievers.items():
        at5 = evaluate_backend(backend=backend, retriever=retriever, cases=cases, ids=ids, k=5)
        at10 = evaluate_backend(backend=backend, retriever=retriever, cases=cases, ids=ids, k=10)
        section.backends[backend.value] = BackendRetrieval(
            cases=at5.overall.cases,
            recall_at_5=round(at5.overall.recall_at_k, 4),
            recall_at_10=round(at10.overall.recall_at_k, 4),
            mrr=round(at5.overall.mrr, 4),
            ndcg_at_5=round(at5.overall.ndcg_at_k, 4),
        )
        for result in at5.results:
            if result.recall_at_k == 0.0:
                failures.append(
                    Failure(
                        component=ErrorComponent.RETRIEVAL,
                        severity=Severity.S2,
                        kind="retrieval_miss_at_5",
                        detail=f"{backend.value} {result.query_id}: no relevant entity in top 5",
                    )
                )
    semantic = [section.backends[b.value] for b in SEMANTIC_BACKENDS if b.value in section.backends]
    section.semantic_recall_at_5 = max((b.recall_at_5 for b in semantic), default=None)
    section.status = (
        SectionStatus.RUN
        if semantic
        else SectionStatus.PARTIAL
        if section.backends
        else SectionStatus.NOT_RUN
    )
    if not semantic:
        section.not_run_reason = not_run_reason
    return section

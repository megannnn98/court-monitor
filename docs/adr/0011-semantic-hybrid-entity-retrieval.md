# ADR 0011: Semantic Hybrid Entity Retrieval

## Status

Accepted. Partially supersedes [ADR 0002](0002-drop-dense-hybrid-search.md):
dense/hybrid retrieval returns, but on canonical entities, not on article chunks.

## Context

Structured research (ADR 0008–0010) answers exact criteria: person id, name,
persecution status, Rosfinmonitoring status, snapshot, event types, dates,
source. It cannot answer descriptive questions:

| Query | PostgreSQL filters can do | Needs semantic similarity |
|---|---|---|
| «люди, которых преследовали за антивоенные высказывания» | `persecution_status=political`, RF status | «высказывания против войны» ↔ «осуждал вторжение», «пост о бомбардировках», «слова о Буче» |
| «задержания на уличных протестах» | `event_types=[detention]`, dates | «уличный протест» ↔ «одиночный пикет», «плакат на площади», «митинг против мобилизации» |
| «наказание за высказывания в интернете» | event types, source | ↔ «пост во ВКонтакте», «видео в Telegram», «репост картинки» |
| «гонения за веру» | — (no filter by reason) | ↔ «участие в организации «Свидетели Иеговы»» |

Persecution reasons are free-text strings, events carry only a type, a date
and an extracted span. The previous dense stack (ADR 0001) indexed article
chunks; it was removed with `ArticleChunk` (ADR 0002).

## Decision

**PostgreSQL is the source of truth. Qdrant is a semantic candidate index.**

```text
ResearchRequest (criteria.semantic_query)
      ↓
ResearchPlanner — deterministic routing:
      semantic_query → hybrid (hybrid_reranked if SEMANTIC_RERANK=1)
      anything else  → structured (Qdrant is never touched)
      ↓
retrieve_candidates (LangGraph node → EntityRetriever)
      Lexical: PostgreSQL full-text over semantic_documents ─┐
      Dense:   E5 query embedding → Qdrant persons_semantic ─┼→ RRF (k=60) → [cross-encoder]
                                                             ┘
      ↓ candidate person ids (≤ SEMANTIC_CANDIDATE_POOL_SIZE, default 100, max 200)
ResearchService.execute(request, candidate_person_ids=…)
      every criterion applied from PostgreSQL; facts loaded from PostgreSQL
      ↓
ResearchReport (retrieval metadata, no scores)
```

### Entity-level, not article chunks

Retrieval objects are canonical **Person** and **Event** (there is no
canonical Case). A semantic document is a deterministic text built only from
structured data linked to the entity:

- Person: canonical name, aliases, latest persecution classification (status,
  reasons, evidence types as labels), linked events with type, date, own
  extracted span and linked court/location/legal reference;
- Event: type, date, span, participants with roles, court/location/legal
  reference, source name.

Never a whole article (spans are capped at 400 chars), never LLM output, sorted
everywhere. `content_hash = sha256(entity_type, representation_version, text)`.
Articles stay provenance; chunking articles would again make the index
disagree with the research object and mix several people in one vector.

### Storage

- `semantic_documents` (PostgreSQL, migration `m7n8o9p0q1r2`): derived,
  rebuildable text per entity with `representation_version`, `content_hash`,
  `indexed_at` and a Russian `tsvector` (GIN). Lexical retrieval, the reranker
  and incremental indexing all use it, so lexical and dense see the same text.
- Qdrant collections `persons_semantic`, `events_semantic` (cosine). Point id =
  `uuid5(fixed namespace, "<entity_type>:<entity_id>")`: re-indexing overwrites.
  Payload: `entity_id`, `entity_type`, `representation_version`,
  `content_hash` only.
- `SemanticIndexer`: full rebuild (recreate collection) or incremental (embed
  only documents whose hash/version changed or were never indexed); stale
  entities are deleted after a full scan. A failed embedding leaves the document
  pending for the next run.

### Retrieval

- `EntityRetriever.retrieve(RetrievalQuery) -> RetrievalResult`; `RetrievalHit`
  holds entity type/id, backend, rank, score and component ranks — no facts.
- Lexical: `to_tsquery('russian', w1 | w2 | …)` over `semantic_documents`
  (OR, so partial descriptive overlap counts), `ts_rank_cd`.
- Dense: `TextEmbedder` port; `SentenceTransformerEmbedder` with
  `intfloat/multilingual-e5-base` (`query:` / `passage:` prefixes, normalized
  vectors, `EMBEDDING_DEVICE=auto|cpu|cuda`).
- Hybrid: RRF ported from the removed chunk-level `rrf.py`, keyed by
  `(entity_type, entity_id)`, deterministic tie-breaks.
- Reranker: `Reranker` port; `CrossEncoderReranker`
  (`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`) scores (query, semantic
  document) pairs. Opt-in (`SEMANTIC_RERANK=1`), see evaluation.
- `sentence-transformers`/torch are in the optional uv group `semantic` and
  imported lazily; structured research never loads a model.

### Semantic intent in the request

One field: `PersonResearchCriteria.semantic_query`. Intake sets it only for a
free-text description of activity/circumstances. Person attributes that must be
verified (occupation, age, region, …) stay `unsupported_criteria` →
clarification; semantic search is never used to imitate facts the database
cannot check. `POST /research` (structured endpoint, no retrieval) rejects
`semantic_query` with 422 instead of ignoring it; `ResearchService.execute`
raises if it gets `semantic_query` without candidate ids.

### Failures are not empty results

If a plan needs semantic retrieval and Qdrant is unreachable, the collection is
missing, the model cannot load, CUDA runs out of memory or vector sizes differ,
the workflow fails with `semantic_retrieval_unavailable` (HTTP 503, CLI exit 2);
without `QDRANT_URL` it fails with `semantic_retrieval_not_configured`. No
lexical-only fallback. Requests without `semantic_query` do not depend on
Qdrant at all.

### Retrieval score ≠ domain confidence

Retrieval scores (ts_rank, cosine, RRF, cross-encoder logits) only order
candidates. They never become a persecution/match/identity confidence, never
waive or trigger human review, and the report shows only mode, pool size and a
rank labelled "similarity, not a fact". `ResearchQueryResult.retrieval` keeps
scores for debugging.

## Evaluation

Entity-level corpus (`tests/fixtures/entity_retrieval_corpus.json`): 18 persons,
18 events, synthetic Russian event texts. 11 cases
(`entity_retrieval_cases.json`), graded relevance 0/1/2, 9 person + 2 event
cases; 5 are `semantic_only` — a test asserts that lexical retrieval finds no
relevant entity for them. `evaluate-retrieval` seeds a disposable database,
builds documents with the production builders, indexes them and runs each
backend separately. Result (k = 5, real models on CUDA, 2026-09-14):

| backend | MRR | Recall@5 | nDCG@5 | P@5 | MRR (semantic-only) | Recall@5 (semantic-only) | nDCG@5 (semantic-only) |
|---|---|---|---|---|---|---|---|
| lexical | 0.545 | 0.371 | 0.408 | 0.218 | 0.000 | 0.000 | 0.000 |
| dense | 0.920 | 0.762 | 0.823 | 0.509 | 0.825 | 0.700 | 0.755 |
| hybrid | 0.920 | 0.780 | 0.851 | 0.527 | 0.825 | 0.700 | 0.755 |
| hybrid_reranked | 0.875 | 0.632 | 0.650 | 0.418 | 0.825 | 0.500 | 0.483 |

Consequences for the design:

- Dense and hybrid clearly beat lexical, most of all on semantic-only queries:
  semantic retrieval is kept.
- Hybrid ≥ dense on every overall metric (lexical helps keyword-heavy queries
  like «акции против военкоматов»): hybrid is the workflow default.
- The mmarco cross-encoder **lowers** Recall@5/nDCG@5 on this corpus: the
  reranker stays implemented but off by default (`SEMANTIC_RERANK=1` to enable).
- All backends fail «бытовые правонарушения» (non-political offences): a
  known gap.
- The corpus is small and written by the same authors as the queries; the
  numbers show relative behaviour, not production quality.

## Relation to Entity Resolution v2

Dense person retrieval is reusable for candidate generation in ER v2, but
"semantically similar person" is not "same person": this part never merges or
links persons based on similarity.

## Consequences

- New dependency `qdrant-client`; optional group `semantic`
  (`sentence-transformers`). mypy ignores missing `sentence_transformers`/`torch`
  imports so CI without the group type-checks.
- New tables/migration: `semantic_documents`.
- New config: `QDRANT_URL`, `PERSON_QDRANT_COLLECTION`,
  `EVENT_QDRANT_COLLECTION`, `EMBEDDING_MODEL_ID`, `EMBEDDING_DEVICE`,
  `EMBEDDING_BATCH_SIZE`, `RERANKER_MODEL_ID`, `RERANKER_DEVICE`,
  `SEMANTIC_RERANK`, `SEMANTIC_CANDIDATE_POOL_SIZE`, `EVALUATION_DATABASE_URL`.
- The semantic index is not refreshed automatically: after ingestion,
  resolution or classification run `rebuild-semantic-index --incremental`.
- Indexing has no cross-process lock: do not run a full rebuild concurrently
  with another rebuild/index run (a full rebuild recreates the collection).
- Model loading is guarded by a per-instance lock, so concurrent first
  semantic requests in the FastAPI thread pool load each model once.
- Dense retrieval has no relevance threshold: the pool is the top-N nearest
  entities, so with semantic_query the result means "matching criteria among
  the N most similar persons", as the report states.

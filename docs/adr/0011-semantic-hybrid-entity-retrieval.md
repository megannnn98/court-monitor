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
      ↓ retrieved candidates (≤ SEMANTIC_CANDIDATE_POOL_SIZE, default 100, max 200)
accept_candidates (LangGraph node → SemanticRelevancePolicy)
      accepted only if dense cosine similarity ≥ SEMANTIC_DENSE_MIN_SCORE
      ↓ accepted person ids (possibly [])
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
  `content_hash`, `embedding_model_id` only.
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

### Retrieval ranking vs. semantic relevance acceptance

**A nearest neighbour is not automatically relevant.** Qdrant always returns
the closest vectors, also for a query unrelated to every entity («выращивание
бананов на Марсе»). Treating that pool as "semantic criterion matched" would
show unrelated people. Two separate steps:

- *Retrieval* (lexical + dense + RRF) only ranks candidates.
- *Acceptance* (`DenseSimilarityRelevancePolicy`) decides which candidates
  satisfy `semantic_query`: a hit is accepted only if its dense cosine
  similarity (the dense hit's own score, or the dense component kept on fused
  and reranked hits in `component_scores`) is ≥ `SEMANTIC_DENSE_MIN_SCORE`.
  RRF scores, lexical ranks and reranker scores never accept a candidate; a
  lexical-only hit (no dense score) is rejected. Scores of different backends
  are never compared to one threshold.
- The decision keeps both levels (`SemanticRetrievalDecision`: retrieved,
  accepted, rejected count, threshold, model) in
  `ResearchQueryResult.semantic_acceptance`.
- Only accepted ids reach `ResearchService`. `candidate_person_ids=None` means
  "no restriction" (structured request); `[]` means "no semantic candidate" and
  yields 0 results — never an unrestricted search. All candidates rejected is
  a normal completed result, not a failure; the report says that no entity in
  the current index is similar enough, never that such people do not exist.
  Source routing may still recommend a refresh.

The threshold belongs to the embedding model: the default 0.80 is calibrated
for `intfloat/multilingual-e5-base` only; with another `EMBEDDING_MODEL_ID`,
`SEMANTIC_DENSE_MIN_SCORE` must be set explicitly (configuration error
otherwise). Qdrant points store `embedding_model_id`; searching or
incrementally indexing a collection built by another model (or without that
field) fails with `IndexModelMismatchError` until a full rebuild. Search checks
every returned point; indexing looks for any foreign point in the whole
collection, not a sample.

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

### Acceptance calibration

`evaluate-retrieval` also sweeps dense thresholds over hybrid pools (k-independent):
positive cases measure recall/precision of accepted entities, 15 negative
(off-topic) cases measure rejection and false positives. E5 cosine similarities
are compressed (observed 0.69–0.86); relevant entities 0.76–0.86 overlap with
in-domain irrelevant ones, so the threshold mainly rejects off-topic queries
and cannot raise in-domain precision much. Result (real model, 2026-09-14):

| dense min score | relevant recall | grade-2 recall | semantic-only recall | precision | positive cases with relevant | negative rejection | negative false positives |
|---|---|---|---|---|---|---|---|
| 0.750 | 1.00 | 1.00 | 1.00 | 0.22 | 11/11 | 0.27 | 103 |
| 0.760 | 1.00 | 1.00 | 1.00 | 0.25 | 11/11 | 0.47 | 65 |
| 0.770 | 0.93 | 0.93 | 1.00 | 0.26 | 11/11 | 0.67 | 36 |
| 0.780 | 0.64 | 0.72 | 0.69 | 0.24 | 11/11 | 0.73 | 17 |
| 0.790 | 0.48 | 0.52 | 0.31 | 0.28 | 9/11 | 0.93 | 4 |
| **0.800** (default) | 0.40 | 0.45 | 0.25 | 0.39 | 8/11 | 0.93 | 3 |
| 0.810 | 0.33 | 0.34 | 0.19 | 0.61 | 6/11 | 0.93 | 1 |
| 0.820 | 0.24 | 0.24 | 0.12 | 0.67 | 5/11 | 1.00 | 0 |

0.80 was chosen (project priority: better not to show a person than to show an
unrelated one): no accepted entity for the 14 calibration negatives (highest
similarity 0.789), relevant recall 0.40. A background-calibrated margin (score
minus similarity to off-topic reference documents) was also tested and was not
better (recall 0.40 at zero false positives). The 15th negative, added after
calibration, «задержание кометы телескопом», still leaks (0.815 via the word
«задержание»): shared-keyword nonsense cannot be rejected by a cosine threshold
without losing most recall. Lexical-only acceptance and a reranker threshold
were not introduced (no data supporting them; the reranker stays off).

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
- Relevance acceptance trades recall for precision: with the default
  threshold roughly 60% of relevant entities in the evaluation corpus are not
  accepted, and some in-domain but irrelevant entities still are. The threshold
  is calibrated on a small synthetic corpus.
- New config: `SEMANTIC_DENSE_MIN_SCORE` (required for models other than
  `intfloat/multilingual-e5-base`). Indexes built before `embedding_model_id`
  was stored need a full `rebuild-semantic-index`.

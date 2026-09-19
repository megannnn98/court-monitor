# ADR 0018: pgvector as a second vector store

## Status

Accepted, 2026-09-19, as an experiment. Qdrant stays the default and is not removed;
whether it can go is a separate decision taken on the results below.

## Context

Dense retrieval (ADR 0011) keeps facts in PostgreSQL and candidate vectors in Qdrant.
Qdrant is one more service to run, back up and keep in sync. The question of this stage:
can PostgreSQL with pgvector hold the same derived index with the same quality and an
acceptable speed, so that Qdrant could be dropped later?

Everything semantic already talks to the vector index through one port,
`VectorStore` (`src/semantic_retrieval/vector_store.py`): `SemanticIndexer`, the dense,
hybrid and reranked retrievers never see Qdrant.

## Decision

### Same port, second implementation

`PgVectorStore` (`src/semantic_retrieval/pgvector_store.py`) implements `VectorStore`
with the contract of `QdrantVectorStore`: logical collections, upsert that overwrites an
entity, delete, cosine search with an optional `entity_ids` restriction, the embedding
model check, count, and the same errors (`VectorSizeMismatchError`,
`IndexModelMismatchError`, `RetrievalUnavailableError`). It works through the
application's `sessionmaker`, one transaction per call: no second connection layer.

`SEMANTIC_VECTOR_BACKEND=qdrant|pgvector` (default `qdrant`) picks the store in
`semantic_retrieval/factory.py`, the only place that reads it. The rest of the project
asks the configuration whether semantic retrieval is `enabled` (Qdrant with a URL, or
pgvector) and which Qdrant, if any, readiness should probe.

### Storage

```
semantic_vector_collections(name PK, vector_size, created_at)
semantic_vectors(collection_name FK → collections ON DELETE CASCADE,
                 entity_type, entity_id,               -- PK (collection, type, id)
                 embedding vector,                     -- no fixed dimension
                 embedding_model_id, representation_version, content_hash, updated_at)
```

The rows carry what a Qdrant point carries and nothing more; facts stay in their
tables, and `semantic_documents` keeps the texts.

The embedding column has no dimension, so a model of another size needs no migration.
It is stored PLAIN (migration `u5v6w7x8y9z0`; see "PERSON exact, EVENT HNSW" below).

An HNSW index needs one dimension, so each HNSW collection gets its own partial
expression index, created by the store when the collection is (re)created (the event
collection; the person collection has none, see below):

```sql
CREATE INDEX ix_semvec_hnsw_events_semantic_768 ON semantic_vectors
USING hnsw ((embedding::vector(768)) vector_cosine_ops)
WHERE collection_name = 'events_semantic' AND vector_dims(embedding) = 768;
```

The search repeats that cast and predicate literally, so the planner can use the index.
The `vector_dims` condition keeps rows of another size out of the cast: rows a
recreate deleted in the same transaction are still read by the index build. A new model
means a full rebuild: the collection is recreated with its size, its old index dropped.
Above 2000 dimensions pgvector cannot index `vector`; such a collection is searched
exactly.

Score is `1 − cosine distance`: cosine similarity, higher is closer, as Qdrant's
`Distance.COSINE`. A search within `entity_ids` (a structured candidate set) is exact:
the rows are found by the primary key and their distances computed in PostgreSQL, which
also avoids an approximate scan that stops before it reaches the filtered rows.

A search over a whole HNSW collection sets two things for its own transaction
(`SET LOCAL`):

- `hnsw.ef_search = min(1000, max(40, 4 × limit))`. An HNSW scan returns at most
  `ef_search` rows, and finds more of the true neighbours as it grows. On the working
  corpus, for the default pool of 100: `ef_search` 100 finds 95–97% of the exact top-100
  (worst query 44–78%), 400 finds 99.3–99.7% (worst 96–97%) at 6–9 ms.
- `enable_sort = off`. Right after a bulk load, before autoanalyze, the statistics still
  describe the old table, and the planner twice chose to read every row and sort
  (88 ms and 151 ms instead of ~9 ms, in 2 of 5 benchmark runs). Only the HNSW index
  returns rows already ordered, so without sorts it is always the plan.

### PERSON exact, EVENT HNSW (2026-09-19)

The factory builds `PgVectorStore(exact_collections=[person_collection])`: the person
collection gets no HNSW index and is always searched exactly; the event collection keeps
its HNSW index and the settings above.

Why (`reports/person_search_mode_selection.md`, 45 real person queries on copies of the
working data, against Qdrant): what matters is not ANN recall but the candidates that
pass the relevance threshold and reach the research workflow. Qdrant searches the person
collection exactly (below its indexing threshold). With the current HNSW, pgvector gave
the same accepted set in 16 of 45 queries and the same research result in 29; an HNSW
m=24/ef_construction=200 index at `ef_search` 1000 (pgvector's maximum) matched the sets
in 45 but two orders differed; exact search matched everything, full order included.
The research workflow searches persons only, so exact PERSON reproduces what it gets
today by construction, not by tuning.

The exact query has no `vector_dims` condition: the collection's metadata fixes the
size (upserts are checked against it, a recreate deletes the old rows), and the condition
detoasted every vector a second time. Most of an exact scan's time is reading vectors:
a 768-d vector is 3 KB, above the 2 KB TOAST threshold, so by default it lives out of
line. On 15 k persons an exact top-100 took 57 ms with TOAST and 15 ms with PLAIN
storage (the bare query). `semantic_vectors.embedding` is therefore stored PLAIN. Through
`PgVectorStore`, which also reads the collection's size and returns the model of every
hit, a rebuilt copy of the working data gives p50 20.4 ms, p95 22.5 ms, max 25.8 ms, and
the same top-100 as Qdrant in 45 of 45 queries
(`reports/person_search_modes/production_exact_check.json`).

`SET STORAGE PLAIN` changes only rows written afterwards; it does not rewrite existing
vectors. The full rebuild that switching to pgvector requires (below) writes every vector
anew, so a switched deployment has its person vectors PLAIN. PLAIN keeps a row in one
8 KB heap page, so a much larger embedding model may not fit; that limit is separate
from HNSW's 2000-dimension limit and has to be checked for another model.

Exact search grows linearly with the number of persons (about 1 ms per 1 000 with PLAIN
storage). If it grows several times, the measured fallback is the m=24,
ef_construction=200 HNSW index at `ef_search` 1000 (16 ms today).

### Switching backends needs a full rebuild

Incremental indexing skips documents whose `semantic_documents.indexed_at` is set. That
is one set of marks for every backend: after a Qdrant rebuild, an incremental pgvector
run would skip every document and leave an empty index without a word.
`semantic_index_state(entity_type, vector_backend, rebuilt_at)` records which backend's
full rebuild set the marks. An incremental run (`rebuild --incremental`,
`index_entities`) on another backend raises `IndexBackendMismatchError`; a full rebuild
moves the marks. The check is generic (`VectorStore.backend_name`), not pgvector code in
`SemanticIndexer`. The migration attributes the marks that exist today to Qdrant, so the
running monitoring keeps indexing incrementally.

```
Qdrant ──▶ SEMANTIC_VECTOR_BACKEND=pgvector ──▶ full rebuild-semantic-index ──▶ incremental
```

If Qdrant is removed, the table records one backend and the problem is gone; a per-backend
tracking of marks is not built for a temporary migration.

### Infrastructure

PostgreSQL runs `pgvector/pgvector:pg18-bookworm`, pinned by digest: PostgreSQL 18.6
(the same build as `postgres:18.6-bookworm`) with pgvector 0.8.6 and pg_trgm. The pinned
pgvector tags (`0.8.x-pg18`) carry PostgreSQL 18.4, a downgrade. The migration runs
`CREATE EXTENSION IF NOT EXISTS vector`, so the database image has to change before the
migration runs. CI runs the same image and the Qdrant service; one contract test suite
(`tests/semantic_retrieval/test_vector_store_contract.py`) runs against both stores.

## Results (2026-09-19)

Retrieval evaluation (`evaluate-retrieval`, E5, the fixed synthetic corpus): identical
rankings, top-10 lists and metrics for lexical, dense, hybrid and hybrid_reranked on both
stores (`reports/qdrant_e5_baseline.json`, `reports/pgvector_e5_baseline.json`). The
corpus has 36 documents, so every search there is exact.

Store benchmark (`evaluation/vector_store/benchmark_vector_stores.py`,
`reports/vector_store_benchmark.md`): the working corpus, 8 724 persons and 16 797 events,
the same E5 vectors for both stores. Embedding them takes 89.5 s on the GPU; the store
work below excludes it.

| | Qdrant | pgvector |
|---|---|---|
| full rebuild (store writes), persons / events | 6.0 s / 11.2 s | 25.7 s / 40.7 s |
| 1% changed, persons / events | 0.06 s / 0.10 s | 0.20 s / 0.27 s |
| incremental checks per run | 10 ms | 4–5 ms |
| dense top-100 p50 / p95 | 11.5 / 17.1 ms, 15.2 / 19.9 ms | 9.4 / 10.8 ms, 8.9 / 12.4 ms |
| top-100 within 200 ids p50 / p95 | 8.8 / 13.4 ms, 9.6 / 14.3 ms | 5.8 / 7.2 ms, 6.1 / 7.9 ms |
| overlap@100 with exact, dense | 1.000, 0.9997 | 0.9935, 0.9973 |
| overlap@100, within ids | 1.000 | 1.000 |

Qdrant built no HNSW graph (`indexed_vectors_count` 0): its segments stay below the
default `indexing_threshold`, so it searches exactly. Its working collections take 64 MB
(persons) and 90 MB (events) on disk; pgvector rows and HNSW index take 64 MB and 122 MB.

### Real-corpus validation (2026-09-19)

`reports/pgvector_real_corpus_validation.md`: two copies of the working database, one per
backend, 47 487 documents. Full and incremental rebuilds, updates and deletes behave
identically; the backend switch stops with `IndexBackendMismatchError`; monitoring on
real sources indexes through pgvector; `enable_sort = off` is confirmed necessary (with
partial statistics the planner alone reads 32 k events and sorts, 309 ms). Two findings:
readiness did not know pgvector (fixed), and on the grown person collection pgvector's
HNSW at `ef_search` 400 finds 95% of the exact top-100 while Qdrant searches those
exactly — `ef_search` 1000 gives 99.0%, an m=24, ef_construction=200 index 99.7%. That
led to the PERSON exact decision above.

## Consequences

- Nothing changes for a deployment that does not set `SEMANTIC_VECTOR_BACKEND`.
- With pgvector, the vectors are in the database backup and in the same transactions'
  reach; Qdrant's service, volume and probe become unnecessary.
- Switching the backend costs one full rebuild per entity type; it also writes the person
  vectors PLAIN.
- Person search costs grow linearly with the number of persons (exact).
- Scores differ in the last float digits (Qdrant returns float32, pgvector float64).

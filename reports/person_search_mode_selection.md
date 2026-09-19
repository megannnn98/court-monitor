# Choosing pgvector's PERSON search mode (ADR 0018)

2026-09-19, branch `feat/pgvector-vector-store`. Scripts:
`evaluation/vector_store/person_search_modes.py`,
`evaluation/vector_store/exact_person_variants.py`. Raw results:
`reports/person_search_modes/`. Production code (`pgvector_store.py`), the working
database, the working Qdrant collections and Qdrant were not changed.

## Setup

- A new pgvector copy (`court_monitor_pgv_modes_eval`) restored from the same dump as the
  Qdrant copy, migrated, and given the same test SQL changes; nothing from the monitoring
  rehearsal. Checked equal: 14 926 active persons, 32 546 events, the same md5 over
  persons' ids, names and statuses.
- Reference: the Qdrant copy (`pgv_eval_*` collections), same data. Qdrant searches the
  person collection exactly (no HNSW built below its indexing threshold).
- 45 person queries of `evaluation/vector_store/real_queries.json` (a parity set, not a
  relevance set). EVENT stays on the current HNSW in every mode.
- Each mode: a full rebuild through `SemanticIndexer` (the E5 vectors embedded once and
  cached, 221 s), then for every query: dense top-100, hybrid, the relevance threshold
  (0.80), hybrid + cross-encoder rerank (as `SEMANTIC_RERANK=1`), and the production
  research graph (`build_research_graph` + `ResearchService`) with the LLM parser
  replaced by a stub that passes the query as `semantic_query`. Latency: one warm-up
  pass, then 5 × 45 timed searches.

Modes: **A** HNSW m=16, ef_construction=64 (current), ef_search 400; **B1** HNSW m=24,
ef_construction=200, ef_search 400; **B2** same index, ef_search 1000; **C** exact — no
HNSW index on the person collection at all.

## Results against Qdrant (45 person queries)

Main measure: the candidates past the threshold, and the research workflow's result.

| | A current | B1 m24 ef400 | B2 m24 ef1000 | C exact |
|---|---|---|---|---|
| **accepted (≥ 0.80): same set as Qdrant** | 16 / 45 | 33 / 45 | **45 / 45** | **45 / 45** |
| accepted: same order | 14 / 45 | 30 / 45 | 43 / 45 | **45 / 45** |
| accepted: Jaccard mean / min | 0.949 / 0.667 | 0.990 / 0.925 | 1.000 / 1.000 | 1.000 / 1.000 |
| accepted per query (mean) | 51.5 | 51.6 | 51.8 | 51.8 (Qdrant 51.8) |
| **workflow: same set of persons** | 29 / 45 | 38 / 45 | **45 / 45** | **45 / 45** |
| workflow: same full order | 23 / 45 | 35 / 45 | 43 / 45 | **45 / 45** |
| workflow: same top-5 | 39 / 45 | 44 / 45 | 45 / 45 | 45 / 45 |
| workflow: same top-10 | 35 / 45 | 44 / 45 | 45 / 45 | 45 / 45 |
| workflow: Jaccard mean / min | 0.950 / 0.667 | 0.985 / 0.905 | 1.000 / 1.000 | 1.000 / 1.000 |
| workflow: same status | 45 / 45 | 45 / 45 | 45 / 45 | 45 / 45 |
| reranked: same full order | 11 / 45 | 34 / 45 | 45 / 45 | 45 / 45 |
| reranked: same top-10 order | 36 / 45 | 44 / 45 | 45 / 45 | 45 / 45 |
| dense overlap@10 mean / min | 0.980 / 0.80 | 0.998 / 0.90 | 1.000 / 1.00 | 1.000 / 1.00 |
| dense overlap@100 mean / min | 0.955 / 0.87 | 0.987 / 0.95 | 0.998 / 0.98 | 1.000 / 1.00 |
| exact recall@100 mean / min | 0.955 / 0.87 | 0.987 / 0.95 | 0.998 / 0.98 | 1.000 / 1.00 |
| latency p50 / p95 / max | 8.3 / 10.0 / 11.6 ms | 10.7 / 13.1 / 30.9 ms | 16.4 / 18.6 / 20.8 ms | see below |
| person HNSW index | 61 MB | 81 MB | 81 MB | none |
| vector store writes, full rebuild (persons + events) | 119 s | 168 s | 168 s | 86 s |

The rebuilds ran one after another in one copy; "vector store writes" is upsert +
recreate time (recreate deletes the previous build's rows). Embedding (221 s) is the
same for every mode and excluded. Event index sizes are not comparable across builds
(dead rows of the previous build) and not reported.

B2 differs from Qdrant only in the order of two accepted lists (and the two workflow
results built from them): 99.8% recall still swaps a pair near the tail.

## Exact search: where its time goes

`EXPLAIN (ANALYZE, BUFFERS)` of all 45 queries in mode C: none uses HNSW (no person HNSW
index exists: `indexes_on_semantic_vectors` = events HNSW + primary key); every plan is a
scan of the person rows with a top-N heapsort (`reports/person_search_modes/C_exact_explain.json`).

The cost is reading the vectors, not comparing them: a 768-d vector is 3 KB, above the
2 KB TOAST threshold, so it lives out of line and is detoasted per row.

| exact PERSON query | p50 / p95 / max | buffers per query | same result as C1 |
|---|---|---|---|
| C1 as run in mode C: `collection AND vector_dims(embedding) = 768` | 92 / 108 / 111 ms (131 / 150 / 158 in the mode run, beside the reranker) | 122 k | — |
| C2 collection only (one detoast per row) | 57 / 66 / 70 ms | 63 k | yes |
| C3 C2 with `SET STORAGE PLAIN` (vectors in the heap) | **15.4 / 17.3 / 18.7 ms** | 7.6 k | yes |

`vector_dims` is only needed where an index casts to `vector(N)`; an exact person query
has no index and does not need it. PLAIN storage is a schema change (tested in the copy:
`ALTER COLUMN embedding SET STORAGE PLAIN` + `VACUUM FULL`, 13 s, table 317 MB); it keeps
a row within one 8 KB page, so it holds for vectors up to ~2 000 dimensions — the same
limit HNSW has.

## Choice

By the main measure only B2 and C reproduce Qdrant's candidates and research results
(45 / 45 sets); A and B1 change the research result in 16 and 7 queries of 45.

- **C, exact PERSON** — identical to Qdrant in everything, full order included; no
  person HNSW to build (writes 86 s against 168 s for B2). Latency depends on how it is
  queried: 57 ms without `vector_dims`, 15 ms with PLAIN storage. Scales linearly with
  the number of persons: ≈ 1 ms per 1 000 with PLAIN storage, ≈ 4 ms without.
- **B2, HNSW m=24/ef_construction=200, ef_search 1000** — the same sets, two orders
  differ; 16 ms today and flat as persons grow; a larger index and slower writes;
  ef_search 1000 is pgvector's maximum, so no headroom left for more recall.

Recommendation: **C — PERSON exact, EVENT HNSW**, queried without `vector_dims`; add
PLAIN storage for the embedding column if 57 ms is too slow (it brings C to 15 ms). The
search the research workflow runs then returns what Qdrant returns today, by
construction rather than by tuning. Revisit when the person collection grows several
times; B2 is the fallback with measured numbers.

Nothing was switched, merged or deleted.

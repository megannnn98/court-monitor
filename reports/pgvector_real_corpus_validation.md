# pgvector on the real corpus: validation against Qdrant (ADR 0018)

2026-09-19, branch `feat/pgvector-vector-store`. Raw results: `reports/pgvector_real_corpus/`.
Script: `evaluation/vector_store/validate_real_corpus.py`; queries:
`evaluation/vector_store/real_queries.json`.

## Setup

- One read-only `pg_dump` of the working database, restored twice into a separate
  PostgreSQL 18.6 + pgvector 0.8.6 container: `court_monitor_qdrant_eval` and
  `court_monitor_pgvector_eval`, both migrated to `t4u5v6w7x8y9`. Each backend works on
  its own copy: `semantic_index_state` makes incremental runs of two backends on one
  database different tests.
- Qdrant: the running Qdrant service, collections `pgv_eval_persons_semantic` /
  `pgv_eval_events_semantic`; they are left in place (nothing deleted without a go), the
  working collections were not touched. pgvector: `persons_semantic` / `events_semantic` inside its copy.
- Model `intfloat/multilingual-e5-base` on the GPU; production components throughout
  (`create_semantic_components`, `SemanticIndexer`, dense/hybrid retrievers, relevance
  policy), with the embedder and store wrapped only to time them.
- The working database, its PostgreSQL, the working Qdrant collections and the
  production configuration were not changed.

## 1. IndexBackendMismatchError after switching the backend

Right after the migration both copies record `person=qdrant, event=qdrant` (the working
index's `indexed_at` marks). `SEMANTIC_VECTOR_BACKEND=pgvector rebuild-semantic-index
--incremental` on the pgvector copy:

```
Semantic retrieval unavailable [IndexBackendMismatchError]: The person index was built
in qdrant, not pgvector; run a full rebuild-semantic-index (without --incremental) first
exit code 2
```

Nothing was written: 0 vectors, 0 collections, the marks unchanged. After the full
pgvector rebuild the state became `pgvector`, and the monitoring run below indexed
incrementally through pgvector without an error.

## 2. Full rebuild on the real corpus

14 931 active persons and 32 556 events (47 487 documents) on both copies.

| | total | embedding | vector store writes | building documents in PostgreSQL |
|---|---|---|---|---|
| Qdrant | 334 s | 244 s | 40 s | 51 s |
| pgvector | 419 s | 245 s | 126 s | 48 s |

pgvector writes are 3× slower (HNSW is updated row by row); the rebuild as a whole is
25% longer and stays dominated by embedding.

## 3. Parity of real queries (60 queries: 45 person, 15 event)

Same data on both copies (after the full rebuild; again after the incremental run).
Pool = 100 (`SEMANTIC_CANDIDATE_POOL_SIZE`). "Exact" = exact nearest neighbours computed
in PostgreSQL over the same vectors.

| | full rebuild | after incremental |
|---|---|---|
| dense: top-10 in the same order | 50 / 60 | 47 / 60 |
| dense: overlap@10 mean / min | 0.980 / 0.80 | 0.977 / 0.80 |
| dense: overlap@100 mean / min | 0.957 / 0.85 | 0.955 / 0.85 |
| dense: max score difference, same entity | 3.5e-7 | 3.2e-7 |
| hybrid: top-10 in the same order | 39 / 60 | 40 / 60 |
| hybrid: overlap@100 mean / min | 0.980 / 0.89 | 0.979 / 0.88 |
| hybrid + relevance threshold: identical accepted list | 15 / 60 | 16 / 60 |

Overlap with the exact top-100 (full rebuild):

| | persons (45) | events (15) |
|---|---|---|
| Qdrant | 1.000 (no HNSW built: segments below `indexing_threshold`, exact search) | 0.973 (HNSW) |
| pgvector, `ef_search` 400 | 0.954, worst 0.87 | 0.982, worst 0.95 |

## 4. Where top-k differs, and why

- Every difference is approximate search, never scores: the same entity gets the same
  similarity in both stores (≤ 3.5e-7 apart, float32 vs float64).
- Persons: Qdrant searches exactly, pgvector's HNSW misses 4.6% of the exact top-100 on
  average. The registry cards added since 2026-08 are near-duplicate texts, which makes
  HNSW harder: on the 8 724-person corpus of the first benchmark the same settings found
  99.3%.
- Events: both use HNSW and both miss some; on `e15 «состав преступления 280.3 УК»`
  Qdrant found 85% of the exact top-100, pgvector 96%.
- The accepted lists (dense similarity ≥ 0.80) hold ~54 of the 100 candidates, so a
  swap at the pool's tail changes them: 45 queries differ, 6 of them only in order, by
  4.2 entities on average (at most 22, in `e15`, where Qdrant is the less exact one).

Raising pgvector's recall, persons, 45 queries (latency p50):

| pgvector | recall mean / worst | p50 |
|---|---|---|
| m=16, ef_construction=64 (current), ef_search 400 | 0.954 / 0.87 | 6.3 ms |
| same index, ef_search 800 | 0.982 / 0.94 | 9.3 ms |
| same index, ef_search 1000 (pgvector's maximum) | 0.990 / 0.96 | 11.4 ms |
| m=16, ef_construction=128, ef_search 1000 | 0.993 / 0.97 | 11.7 ms |
| m=24, ef_construction=200, ef_search 1000 | 0.997 / 0.98 | 13.2 ms |
| exact scan (no index) | 1.000 | 52 ms |

## 5. Incremental indexing, update and delete

The same SQL on both copies: 20 persons renamed, 10 persons made inactive (leave the
index), 5 new persons, 20 events change type, 10 unlinked events deleted. Then
`rebuild --incremental` on each copy with its own backend:

| | persons: embedded / unchanged / deleted | events: embedded / unchanged / deleted | vectors after | time (embedding / store) |
|---|---|---|---|---|
| Qdrant | 28 / 14 898 / 10 | 170 / 32 376 / 10 | 14 926 + 32 546 | 46.0 s (1.6 / 0.9) |
| pgvector | 28 / 14 898 / 10 | 170 / 32 376 / 10 | 14 926 + 32 546 | 43.7 s (1.6 / 1.0) |

Identical. More documents are re-embedded than rows changed because a person's document
lists its events and an event's lists its participants. The deleted persons are found by
neither store (search restricted to their ids: empty); each new person is the top dense
hit for its own name in both, with the same top-3. The incremental run is dominated by
building the 47 k documents in PostgreSQL (~42 s), not by the vector store.

## 6. Monitoring through pgvector (real internet)

`SEMANTIC_VECTOR_BACKEND=pgvector monitor --catch-up --source <s> --limit 15` on the
pgvector copy, no `QDRANT_URL`:

| source | discovered | ingested | events | persons | semantic: embedded / unchanged | errors |
|---|---|---|---|---|---|---|
| ovd-info | 15 | 5 | 6 | 25 | 1 664 / 720 | 0 |
| sota-vision | 15 | 0 | 24 | 2 | 34 / 1 864 | 0 |

After it no document is left unindexed and the state stays `pgvector`. A person the run
added (#14937 «Александр Струков») is found by `semantic-search` through pgvector (rank
2; rank 1 is his Memorial registry card #11090 — an entity-resolution duplicate, not a
store issue). The 1 664 re-embeddings come from the classification stage, which
reclassified ~1 400 persons whose documents include the classification.

## 7. Health and readiness (`uvicorn api:app` against the copies)

| scenario | HTTP | status | semantic_retrieval |
|---|---|---|---|
| pgvector, no QDRANT_URL | 200 | ready | ok, "pgvector" |
| pgvector, QDRANT_URL on a dead port | 200 | ready | ok, "pgvector" (Qdrant not probed) |
| pgvector, database unreachable | 503 | unavailable | unavailable |
| qdrant, Qdrant up (control) | 200 | degraded* | ok |
| qdrant, Qdrant down (control) | 200 | degraded | unavailable |

\* monitoring: a stale running run inherited from the dump; unrelated to the store.

Before the fix (commit `b3c02d7`) the first row read `not_configured, "QDRANT_URL not
set"`.

## 8. Query plans; is `enable_sort = off` needed

`EXPLAIN (ANALYZE)` of the dense top-100 as `PgVectorStore` runs it and without its
settings, 60 queries each, on the 47 k-row copy:

| table statistics | store's plan uses HNSW | planner alone uses HNSW | planner alone p50 / max |
|---|---|---|---|
| empty (`reltuples` 0, analyzed before the load) | 60 / 60 | 60 / 60 | 5.4 / 7.5 ms |
| fresh (after ANALYZE) | 60 / 60 | 60 / 60 | 5.6 / 7.8 ms |
| partial: persons analyzed, events loaded after | 60 / 60 | persons 45 / 45, **events 0 / 15** | events **309 / 321 ms** |

The partial state is what a full rebuild produces when autoanalyze runs between the
person and the event collection. The planner then estimates 1 row for the events and
reads them all through the primary key with a sort (309 ms); with the store's settings
the same query takes the HNSW index (14.6 ms p50). **`enable_sort = off` is needed.**
The within-ids search is exact by design in every state: primary key lookup and a sort
of 200 rows, 0.8 ms p50.

## 9. Problems found

1. **Readiness did not know pgvector** — fixed in `b3c02d7` with regression tests:
   semantic retrieval reported `not_configured` under pgvector and could not notice a
   database without pgvector's tables.
2. **pgvector recall on persons is 95%** with `ef_search` 400 (worst query 87%), below
   Qdrant, which searches the persons exactly. Not changed: `ef_search` 1000 gives 99.0%
   (worst 96%) at 11 ms, a better index (m=24, ef_construction=200) 99.7% at 13 ms. A
   choice of recall against latency and write cost, left to a decision.
3. **Accepted results are threshold-sensitive**: with ~54 of 100 candidates above 0.80,
   any recall difference at the pool's tail changes the accepted list. This is true of
   Qdrant's HNSW on events as well.
4. **The working semantic index is stale** (not a pgvector issue): it holds 8 724 persons
   and 16 797 events; a full rebuild of the same data gives 14 931 and 32 556. The
   Memorial registry persons were never indexed — no full rebuild since, and the Dagster
   schedules that would index incrementally are off.
5. **pgvector writes are 3× slower** than Qdrant's (126 s against 40 s for 47 k vectors);
   the full rebuild is 25% longer, still dominated by embedding.
6. Minor: with the database down, readiness says semantic retrieval is unavailable while
   "structured research still works" — structured research is down too then; the overall
   status (503, unavailable) is right.

## 10. Can Qdrant be removed safely

Functionally yes: pgvector passed every check — full and incremental rebuilds identical
to Qdrant, deletes and updates, the backend switch guard, monitoring on real data, health
and readiness (after the fix), plans stable under stale statistics.

Not yet as a like-for-like replacement of today's results: for persons Qdrant currently
searches exactly and pgvector finds 95% of the same top-100, which changes the accepted
lists of most queries. Before removing Qdrant:

1. choose pgvector's recall setting — `ef_search` 1000 (99.0%, 11 ms) or the m=24,
   ef_construction=200 index (99.7%, 13 ms, slower writes) — and re-run this parity;
2. switch one deployment with a full rebuild (`SEMANTIC_VECTOR_BACKEND=pgvector`, then
   `rebuild-semantic-index`), and run monitoring on it for a while;
3. only then drop the Qdrant service, its volume and probe.

Nothing was switched or deleted.

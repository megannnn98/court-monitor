# Qdrant vs pgvector: same E5 vectors

Corpus: `semantic_documents` of the working database, 25521 documents; model `intfloat/multilingual-e5-base` (768 d).
Embedding (measured once, auto): 89.51 s for the documents (285.1 docs/s); query p50 6.998 ms, p95 8.757 ms.

Store times below exclude embedding. Search: top-100; filtered = top-100 within 200 random candidate ids. Overlap@100 = share of the exact (numpy) top-100 found.

| collection | backend | full rebuild, s | index ready wait, s | incr. checks, ms | 1% changed upsert, s | dense p50 / p95, ms | filtered p50 / p95, ms | overlap dense | overlap filtered | HNSW used | size, MB |
|---|---|---|---|---|---|---|---|---|---|---|---|
| person (8724) | qdrant | 6.02 | 0.00 | 9.8 | 0.057 | 11.50 / 17.07 | 8.82 / 13.36 | 1.0000 | 1.0000 | no (0 indexed) | 567.1 |
| person (8724) | pgvector | 25.70 | 0.00 | 4.0 | 0.200 | 9.36 / 10.76 | 5.76 / 7.18 | 0.9935 | 1.0000 | yes | 64.2 |
| event (16797) | qdrant | 11.19 | 0.00 | 10.3 | 0.104 | 15.19 / 19.85 | 9.60 / 14.28 | 0.9997 | 1.0000 | no (0 indexed) | 567.3 |
| event (16797) | pgvector | 40.69 | 0.00 | 5.3 | 0.268 | 8.86 / 12.42 | 6.07 / 7.93 | 0.9973 | 1.0000 | yes | 122.2 |

Notes:

- Qdrant builds HNSW only for segments above `indexing_threshold` (10 000 KB by default); when `indexed_vectors_count` is 0 its searches are exact.
- Qdrant size is its collection directory right after the load (segments not yet merged, preallocated files); pgvector size is the rows' bytes plus the HNSW index.
- pgvector: the store's dense query takes the HNSW index even when the table statistics lag behind a bulk load (`enable_sort = off` in its transaction).

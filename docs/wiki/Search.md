# Search

Общий контракт поиска — `SearchBackend` (`src/search_backend.py`):

```python
search(SearchQuery) -> list[SearchHit]
```

Единственная текущая реализация — `PostgresLexicalSearch`. Dense (`QdrantDenseSearch`), hybrid (`HybridSearch`, RRF) и reranked-hybrid (`RerankingSearch`, `CrossEncoderReranker`) были построены целиком вокруг chunk-уровня и удалены вместе с `ArticleChunk` — см. [ADR 0001](../adr/0001-dual-search-backend.md) (исходное решение, superseded) и [ADR 0002](../adr/0002-drop-dense-hybrid-search.md) (удаление).

`SearchBackend` — протокол, а не заглушка под один backend: `SearchEvaluator` и CLI работают через него, новые реализации можно добавлять не меняя эти слои.

## Lexical: `PostgresLexicalSearch`

`src/postgres_lexical_search.py`.

Использует generated-колонку `parsed_articles.search_vector`:

```text
to_tsvector('russian', text)
```

Поиск строится через:

```text
websearch_to_tsquery('russian', query.text)
```

Матч выполняется через `search_vector @@ search_query`, ранжирование — через `ts_rank_cd(...)`. Результат — вся статья целиком (`SearchHit.text` = `ParsedArticleRecord.text`), не фрагмент.

Веса по полям (например, повышенный вес для `title`) не реализованы — поиск строится только по `text`, расширение на title-boost не входило в объём последнего рефакторинга.

Преимущество lexical backend — отсутствие отдельного поискового индекса вне PostgreSQL.

Ограничение — поиск зависит от лексического совпадения и может пропускать семантически близкие формулировки. Семантический (dense) поиск можно будет вернуть позже отдельной задачей, на article-level embeddings.

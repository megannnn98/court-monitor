# Search

Общий контракт — `Protocol SearchBackend` (`src/search_backend.py`): `search(SearchQuery) -> list[SearchHit]`. Две независимые реализации выбираются флагом `--backend` в CLI (`lexical` по умолчанию, `dense`). Почему два backend'а, а не один — [ADR 0001](../adr/0001-dual-search-backend.md).

## Lexical: `PostgresLexicalSearch`

`src/postgres_lexical_search.py`. Использует generated-колонку `article_chunks.search_vector` (`to_tsvector('russian', text)`, см. [Data-Model](Data-Model.md)):

- запрос: `websearch_to_tsquery('russian', query.text)` — поддерживает синтаксис вида `"точная фраза"`, `-исключение`, `OR`
- матч: `search_vector @@ search_query`
- ранжирование: `ts_rank_cd(...)`, сортировка `score DESC, chunk_id ASC` (детерминированный tie-break)
- join через `article_chunks → parsed_articles → source_documents → sources`, чтобы собрать `SearchHit` (title, url, source_base_url и т.д. одним запросом)

Работает целиком в той же Postgres, где лежат данные — не требует отдельного индекса вне БД.

## Dense: `QdrantDenseSearch` + `QdrantChunkIndexer`

`src/qdrant_dense_search.py`, `src/qdrant_chunk_indexer.py`, `src/text_embedder.py`, `src/dense_config.py`.

- **Эмбеддинг** — `TextEmbedder` (`sentence_transformers.SentenceTransformer`, `EMBEDDING_MODEL_ID` из env), `cuda` если доступна, иначе `cpu`. Запросы и документы эмбеддятся с разными префиксами: `"query: {text}"` vs `"passage: {text}"` — модель инструкционная (E5-подобная), нормализованные векторы.
- **Индексация** (`QdrantChunkIndexer`) — читает **все** `article_chunks` из Postgres одним запросом, эмбеддит батчем, upsert'ит в Qdrant с `id = chunk_id`. Payload дублирует все поля `SearchHit` кроме `score`, чтобы поиск не ходил обратно в Postgres.
- **Коллекция**: `vector_size` передаётся явно (в `main.py` — `768`, должен совпадать с реальной размерностью `EMBEDDING_MODEL_ID`, ничем не проверяется); `Distance.COSINE`. `recreate_collection()` — удаляет и создаёт заново (полная переиндексация); `ensure_collection()` — создаёт только если нет (инкрементальный путь, сейчас не используется в CLI).
- **Поиск** (`QdrantDenseSearch.search`) — эмбеддит запрос, `client.query_points(...)`, собирает `SearchHit` из `payload` + `point.score` (косинусная близость, не `ts_rank_cd` — шкалы `score` между backend'ами **не сопоставимы**).

## Синхронизация индексов

Qdrant не обновляется автоматически при `ingest` — переиндексация (`indexer.recreate_collection(); indexer.index_all()`) в текущем коде запускается только внутри `evaluate-search --backend dense` (`main.py`), отдельной команды индексации для обычного `ingest`-потока нет.

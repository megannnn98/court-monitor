# Удаление ArticleChunk и dense/hybrid/reranked-hybrid поиска

Домен упрощён: `ArticleChunk` (и таблица `article_chunks`) убраны из модели и БД, `ParsedArticle.text` стал единственным source of truth содержимого статьи. Dense search (`QdrantDenseSearch`, `QdrantChunkIndexer`, `TextEmbedder`), hybrid (`HybridSearch`, RRF) и reranked-hybrid (`RerankingSearch`, `CrossEncoderReranker`) — см. [ADR 0001](0001-dual-search-backend.md) — были построены исключительно вокруг chunk-уровня (payload в Qdrant, RRF dedup-ключ, cross-encoder candidates — всё оперировало `chunk_id`/`ordinal`). Адаптация этого стека на article-level embeddings — отдельная задача, выходящая за рамки удаления chunks, поэтому стек удалён целиком, а не переписан или временно отключён с полурабочим кодом внутри репозитория.

Текущий поиск — только `PostgresLexicalSearch` (tsvector/GIN по `parsed_articles.text`, `russian` конфигурация). `SearchBackend` (`Protocol`) сохранён без изменений — им пользуется `SearchEvaluator`, и он допускает добавление новых реализаций в будущем.

Dense/hybrid retrieval можно будет вернуть позже отдельной задачей — на article-level embeddings, а не через возврат `ArticleChunk`.

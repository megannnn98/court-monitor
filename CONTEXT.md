# court-monitor

Пайплайн загрузки статей ОВД-Инфо (ovd.info), их разбора, сохранения и полнотекстового/семантического поиска по фрагментам.

## Language

**SourceReference**:
Ссылка на публикацию до загрузки — внешний идентификатор и URL, переданные вызывающей стороной (CLI).
_Avoid_: URL, ссылка

**RawDocument**:
Сырой HTTP-ответ по `SourceReference` — байты содержимого, content-type и время загрузки, ещё не разобранные.
_Avoid_: HTML, ответ

**ParsedArticle**:
Статья после разбора HTML: заголовок, дата публикации, полный текст. Одна статья соответствует одному `RawDocument`.
_Avoid_: документ, статья (без уточнения стадии)

**ArticleChunk**:
Один фрагмент текста статьи — параграф, полученный разбиением `ParsedArticle.text` по `\n\n`, с порядковым номером (`ordinal`).
_Avoid_: фрагмент, параграф (используй только в описательном тексте, не как термин)

**Source**:
Источник публикаций верхнего уровня (например, ovd.info) — имя и базовый URL. Хранится в таблице `sources`.

**SourceDocument**:
Запись о загруженной публикации в БД: внешний ID, канонический URL, сырое содержимое. Персистентный аналог `RawDocument`, связан с `Source`.
_Avoid_: документ (без уточнения — используй только когда стадия ясна из контекста)

**IngestionPipeline**:
Оркестратор конвейера: `WebsiteAdapter.fetch` → `OvdInfoArticleParser.parse` → `Chunker.split` → `IngestionPersistence.save`. Результат — `IngestionResult`.

**SearchQuery / SearchHit**:
`SearchQuery` — текст запроса и лимит выдачи, общий для обоих поисковых backend'ов. `SearchHit` — один найденный `ArticleChunk` с оценкой релевантности (`score`), общий формат для lexical и dense поиска.

**SearchBackend**:
Общий протокол (`Protocol`) поиска: принимает `SearchQuery`, возвращает `list[SearchHit]`. Две реализации: `PostgresLexicalSearch` (lexical) и `QdrantDenseSearch` (dense). См. [ADR 0001](docs/adr/0001-dual-search-backend.md).

**Lexical search**:
Поиск по `tsvector`-индексу PostgreSQL (`websearch_to_tsquery`, конфигурация `russian`). Точное совпадение словоформ/лемм, без учёта семантики.
_Avoid_: полнотекстовый поиск (используй только описательно)

**Dense search**:
Семантический поиск по векторам эмбеддингов в Qdrant (косинусная близость). Находит смысловые совпадения без точного совпадения слов.
_Avoid_: векторный поиск (используй только описательно)

**Evaluation case / Evaluation report**:
`EvaluationCase` — тестовый запрос с ожидаемым `ChunkReference`. `EvaluationReport` — результат прогона всех кейсов через `SearchBackend` с метрикой `mean_reciprocal_rank`.

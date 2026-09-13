# court-monitor

Пайплайн загрузки статей ОВД-Инфо (ovd.info), их разбора, сохранения и полнотекстового поиска.

## Language

**SourceReference**:
Ссылка на публикацию до загрузки — внешний идентификатор и URL, переданные вызывающей стороной (CLI).
_Avoid_: URL, ссылка

**RawDocument**:
Сырой HTTP-ответ по `SourceReference` — байты содержимого, content-type и время загрузки, ещё не разобранные.
_Avoid_: HTML, ответ

**ParsedArticle**:
Статья после разбора HTML: заголовок, дата публикации, полный очищенный текст. Одна статья соответствует одному `RawDocument`. `text` — единственный source of truth содержимого статьи; разбиение на фрагменты (chunking) в доменную модель и в БД не входит — если конкретному алгоритму понадобятся фрагменты, они вычисляются временно в памяти (`split(article.text)`) и не сохраняются.
_Avoid_: документ, статья (без уточнения стадии)

**Source**:
Источник публикаций верхнего уровня (например, ovd.info) — имя и базовый URL. Хранится в таблице `sources`.

**SourceDocument**:
Запись о загруженной публикации в БД: внешний ID, канонический URL, сырое содержимое. Персистентный аналог `RawDocument`, связан с `Source`.
_Avoid_: документ (без уточнения — используй только когда стадия ясна из контекста)

**IngestionPipeline**:
Оркестратор конвейера: `WebsiteAdapter.fetch` → `OvdInfoArticleParser.parse` → `IngestionPersistence.save`. Результат — `IngestionResult`.

**SearchQuery / SearchHit**:
`SearchQuery` — текст запроса и лимит выдачи. `SearchHit` — одна найденная статья (`ParsedArticle`) целиком с оценкой релевантности (`score`); идентичность — `source_base_url` + `external_id`.

**SearchBackend**:
Протокол (`Protocol`) поиска: принимает `SearchQuery`, возвращает `list[SearchHit]`. Единственная текущая реализация — `PostgresLexicalSearch`. Ранее в проекте также были dense/hybrid/reranked-hybrid backend'ы поверх Qdrant — удалены вместе с `ArticleChunk`, см. [ADR 0002](docs/adr/0002-drop-dense-hybrid-search.md).

**Lexical search**:
Поиск по `tsvector`-индексу PostgreSQL (`websearch_to_tsquery`, конфигурация `russian`) по полю `ParsedArticle.text`. Точное совпадение словоформ/лемм, без учёта семантики.
_Avoid_: полнотекстовый поиск (используй только описательно)

**Evaluation case / Evaluation report**:
`EvaluationCase` — тестовый запрос с ожидаемым `ArticleReference` (`source_base_url` + `external_id`). `EvaluationReport` — результат прогона всех кейсов через `SearchBackend` с метрикой `mean_reciprocal_rank`.

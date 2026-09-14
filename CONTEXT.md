# court-monitor

Универсальный source layer: обнаружение и загрузка статей из нескольких источников (ОВД-Инфо, SOTA — sota.vision), их разбора, сохранения и полнотекстового поиска.

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

**SourceAdapter**:
Протокол источника: `discover(limit)` находит ссылки на статьи (листинг + pagination) → `list[SourceReference]`; `fetch(reference)` (унаследовано от `DocumentFetcher`) загружает одну статью → `RawDocument`. Один источник = один `SourceAdapter` + один `ArticleParser`, зарегистрированные в `source_registry.py`.

**SourceIngestion**:
Оркестратор пакетной загрузки: `SourceAdapter.discover` → по каждой ссылке `IngestionPipeline.run`. Ошибка одной статьи (`IngestionError`) не прерывает остальные — попадает в `SourceIngestionResult.failures`.

**IngestionPipeline**:
Оркестратор одной статьи: `DocumentFetcher.fetch` → `ArticleParser.parse` → `IngestionPersistence.save`. Результат — `IngestionResult`. Источник-агностичен — конкретный fetcher/parser передаются снаружи.

**SearchQuery / SearchHit**:
`SearchQuery` — текст запроса и лимит выдачи. `SearchHit` — одна найденная статья (`ParsedArticle`) целиком с оценкой релевантности (`score`); идентичность — `source_base_url` + `external_id`.

**SearchBackend**:
Протокол (`Protocol`) поиска: принимает `SearchQuery`, возвращает `list[SearchHit]`. Единственная текущая реализация — `PostgresLexicalSearch`. Ранее в проекте также были dense/hybrid/reranked-hybrid backend'ы поверх Qdrant — удалены вместе с `ArticleChunk`, см. [ADR 0002](docs/adr/0002-drop-dense-hybrid-search.md).

**Lexical search**:
Поиск по `tsvector`-индексу PostgreSQL (`websearch_to_tsquery`, конфигурация `russian`) по полю `ParsedArticle.text`. Точное совпадение словоформ/лемм, без учёта семантики.
_Avoid_: полнотекстовый поиск (используй только описательно)

**Evaluation case / Evaluation report**:
`EvaluationCase` — тестовый запрос с ожидаемым `ArticleReference` (`source_base_url` + `external_id`). `EvaluationReport` — результат прогона всех кейсов через `SearchBackend` с метрикой `mean_reciprocal_rank`.

**ResearchRequest / ResearchService**:
`ResearchRequest` — структурированный детерминированный запрос: `object_type` (сейчас только `person`), `PersonResearchCriteria`, `limit`. `ResearchService.execute()` отвечает на него через существующие domain services (для `political` + RF-статуса — `CandidateQueryService`) и возвращает `ResearchResponse`. Natural language/LLM — выше, в адаптерах. См. [ADR 0008](docs/adr/0008-research-domain-and-research-service.md).
_Avoid_: поиск (для research — это не lexical search), запрос к LLM

**Research result / Evidence**:
`PersonResearchResult` — результат по канонической `Person`: алиасы, связанные события, классификация, RF-статус по snapshot, `warnings` и `review_required`. `ResearchEvidence` — span статьи (offsets + текст span), подтверждающий факт именно об этом человеке; статья — только provenance (`ResearchSource`), не результат.
_Avoid_: статья как результат

**Research workflow / Request intake**:
LangGraph-граф, превращающий natural-language запрос в `ResearchRequest` и выполняющий его через `ResearchService`. Request intake — единственный шаг с LLM (Together AI): извлекает структурированный запрос, `unsupported_criteria` и вопрос для уточнения; факты не создаёт. Результат — `ResearchQueryResult` со статусом `completed` / `clarification_required` / `failed`. См. [ADR 0009](docs/adr/0009-langgraph-research-orchestration.md).
_Avoid_: агент (автономного цикла нет), ответ LLM

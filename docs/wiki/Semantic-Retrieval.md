# Semantic Retrieval

Семантический поиск кандидатов по каноническим сущностям (Person, Event). PostgreSQL — источник фактов, Qdrant — только индекс кандидатов. Решение — [ADR 0011](../adr/0011-semantic-hybrid-entity-retrieval.md).

```plantuml
@startuml
title Semantic candidate retrieval in research

rectangle "ResearchRequest\ncriteria.semantic_query" as Req
component "ResearchPlanner\n(deterministic routing)" as Plan
component "retrieve_candidates\n(LangGraph node)" as Node
component "PostgresLexicalEntityRetriever\n(semantic_documents tsvector)" as Lex
component "QdrantEntityRetriever\n(E5 query → persons_semantic)" as Dense
component "RRF k=60\n(dedup by entity)" as RRF
component "accept_candidates\nSemanticRelevancePolicy\n(dense cosine ≥ threshold)" as Accept
component "CrossEncoderReranker\n(opt-in)" as Rerank
component "ResearchService.execute\n(candidate_person_ids)" as Service
database "PostgreSQL" as PG
database "Qdrant" as Q
rectangle "ResearchReport\n(retrieval metadata, без scores)" as Report

Req --> Plan
Plan --> Node : semantic_query
Plan --> Service : structured
Node --> Lex
Node --> Dense
Lex --> PG
Dense --> Q
Lex --> RRF
Dense --> RRF
RRF --> Rerank
RRF --> Accept : retrieved
Rerank --> Accept : retrieved
Accept --> Service : accepted ids (может быть [])
Service --> PG
Service --> Report
@enduml
```

## Когда используется

| Критерий | Путь |
|---|---|
| `person_id`, `name`, `persecution_status`, `rosfinmonitoring_status`, `snapshot_id`, `event_types`, `date_from/date_to`, `source` | structured (Qdrant не нужен) |
| `semantic_query` (описание деятельности/обстоятельств) | hybrid → кандидаты → те же structured-фильтры |
| профессия, возраст, регион, … | `unsupported_criteria` → clarification; semantic search их не заменяет |

## Ранжирование ≠ релевантность

Ближайший сосед не означает релевантный: Qdrant вернёт кандидатов и на «выращивание бананов на Марсе». Поэтому после retrieval узел `accept_candidates` применяет `DenseSimilarityRelevancePolicy`:

- кандидат принят, только если **dense cosine similarity ≥ `SEMANTIC_DENSE_MIN_SCORE`** (для hybrid/reranked берётся dense-компонента из `component_scores`);
- RRF score, lexical rank и cross-encoder score кандидата не принимают; lexical-only hit отклоняется;
- в `ResearchService` уходят только принятые id; `candidate_person_ids=[]` → 0 результатов (не поиск по всем), `None` — structured-запрос без ограничения;
- все отклонены — нормальный `completed` с отчётом «В текущем индексе не найдено сущностей с достаточной семантической релевантностью запросу…», не ошибка;
- `ResearchQueryResult.semantic_acceptance` хранит retrieved, accepted, число отклонённых, порог и модель.

Порог привязан к модели: `0.80` откалиброван для `intfloat/multilingual-e5-base`. Для другой `EMBEDDING_MODEL_ID` без явного `SEMANTIC_DENSE_MIN_SCORE` — ошибка конфигурации. В payload Qdrant хранится `embedding_model_id`; индекс другой модели (или старый, без поля) → `IndexModelMismatchError` до полного rebuild.

## Semantic documents

Детерминированный текст из структурированных данных, таблица `semantic_documents` (производная, пересобираемая).

Person:

```text
Персона: Игорь Волков.
Классификация преследования: политическое. Основания: Политическая статья: 207.3. Признаки: политическое обвинение.
События:
- приговор, 2023-02-01: Игоря Волкова приговорили к семи годам колонии по статье 207.3 УК за видео в Telegram, где он осуждал вторжение российских войск в Украину. Правовое основание: статье 207.3 УК.
```

Event:

```text
Событие: задержание, 2022-03-09.
Фрагмент: Виктора Голубева задержали, когда он пришёл к памятнику Шевченко с цветами и сине-жёлтой лентой.
Участники: Виктор Голубев (subject).
Место: памятнику Шевченко.
Источник: SOTA.
```

Только данные, связанные с сущностью; фрагмент — span события (≤ 400 символов), не статья. У персоны также до 3 предложений её собственных упоминаний (representation v2, ADR 0011 amendment). Смена формата → увеличить `PERSON_REPRESENTATION_VERSION`/`EVENT_REPRESENTATION_VERSION`.

## Модули (`src/semantic_retrieval/`)

| Модуль | Роль |
|---|---|
| `models.py` | `RetrievalQuery`, `RetrievalHit`, `RetrievalResult`, `SemanticDocument`, `RetrievalBackend`, ошибки |
| `documents.py` | `PersonSemanticDocumentBuilder`, `EventSemanticDocumentBuilder`, `compute_content_hash` |
| `document_store.py` | `SqlAlchemySemanticDocumentRepository`, `PostgresLexicalEntityRetriever` |
| `vector_store.py` | `VectorStore`, `QdrantVectorStore`, `point_id` |
| `pgvector_store.py` | `PgVectorStore` — тот же контракт на PostgreSQL + pgvector ([ADR 0018](../adr/0018-pgvector-vector-store.md)) |
| `embeddings.py` | `TextEmbedder`, `SentenceTransformerEmbedder` (E5, device) |
| `reranking.py` | `Reranker`, `CrossEncoderReranker` |
| `retrievers.py` | `EntityRetriever`, `QdrantEntityRetriever`, `HybridEntityRetriever`, `RerankingEntityRetriever` |
| `rrf.py` | `reciprocal_rank_fusion` |
| `indexer.py` | `SemanticIndexer` (rebuild / incremental / delete) |
| `metrics.py`, `evaluation.py` | MRR, Recall@k, Precision@k, nDCG@k; корпус и сравнение backend'ов |
| `factory.py` | конфигурация из env, выбор vector store (`SEMANTIC_VECTOR_BACKEND`), сборка компонентов |

## Команды

```bash
docker compose --profile semantic up -d      # Qdrant
uv sync --group semantic                     # sentence-transformers + torch

uv run python src/main.py rebuild-semantic-index --entity person
uv run python src/main.py rebuild-semantic-index --entity event --incremental --batch-size 64
uv run python src/main.py semantic-search "уличные протесты" --entity person --backend hybrid

# сравнение backend'ов на фиксированном корпусе (одноразовая БД *_test/*_eval, Qdrant in-memory)
uv run python src/main.py evaluate-retrieval --backend all --k 5 \
  --database-url postgresql+psycopg://court_monitor:court_monitor_dev@localhost:5433/court_monitor_test
```

`evaluate-retrieval` очищает таблицы указанной БД и отказывается работать с базой, чьё имя не оканчивается на `_test`/`_eval`. `--vector-backend pgvector` строит индекс в этой же БД вместо Qdrant.

## Vector store: Qdrant или pgvector

`SEMANTIC_VECTOR_BACKEND=qdrant` (по умолчанию) хранит векторы в Qdrant, `pgvector` — в PostgreSQL приложения (таблицы `semantic_vector_collections`, `semantic_vectors`, по частичному HNSW-индексу `(embedding::vector(N))` на коллекцию). Индексатор, retrievers и reranker одинаковы для обоих. Сравнение на одних и тех же векторах E5: [ADR 0018](../adr/0018-pgvector-vector-store.md), `reports/qdrant_e5_baseline.json`, `reports/pgvector_e5_baseline.json`, `reports/vector_store_benchmark.md`.

Смена backend'а требует **полного** rebuild каждой сущности: отметки `indexed_at` общие, и `semantic_index_state` помнит, чей полный rebuild их поставил. Инкрементальный rebuild чужого индекса — `IndexBackendMismatchError`, а не молча пустой индекс:

```bash
SEMANTIC_VECTOR_BACKEND=pgvector uv run python src/main.py rebuild-semantic-index --entity all
SEMANTIC_VECTOR_BACKEND=pgvector uv run python src/main.py rebuild-semantic-index --entity all --incremental
```

## Результаты evaluation (k = 5, реальные модели, 2026-09-14)

| backend | MRR | Recall@5 | nDCG@5 | P@5 | MRR (semantic-only) | Recall@5 (semantic-only) | nDCG@5 (semantic-only) |
|---|---|---|---|---|---|---|---|
| lexical | 0.545 | 0.371 | 0.408 | 0.218 | 0.000 | 0.000 | 0.000 |
| dense | 0.920 | 0.762 | 0.823 | 0.509 | 0.825 | 0.700 | 0.755 |
| hybrid | 0.920 | 0.780 | 0.851 | 0.527 | 0.825 | 0.700 | 0.755 |
| hybrid_reranked | 0.875 | 0.632 | 0.650 | 0.418 | 0.825 | 0.500 | 0.483 |

18 persons, 18 events, 11 ranking-запросов (5 semantic-only без лексического пересечения — это проверяется тестом) и 15 negative (off-topic) запросов, которые в ranking-метриках не участвуют. Reranker на этом корпусе ухудшает качество, поэтому по умолчанию выключен. Корпус маленький и синтетический.

### Relevance acceptance (hybrid pools)

Наблюдаемая dense similarity E5: 0.69–0.86; релевантные 0.76–0.86 пересекаются с нерелевантными внутри тематики, поэтому порог в основном отсекает off-topic запросы.

| dense min score | relevant recall | grade-2 recall | semantic-only recall | precision | positive cases with relevant | negative rejection | negative false positives |
|---|---|---|---|---|---|---|---|
| 0.750 | 1.00 | 1.00 | 1.00 | 0.22 | 11/11 | 0.27 | 103 |
| 0.760 | 1.00 | 1.00 | 1.00 | 0.25 | 11/11 | 0.47 | 65 |
| 0.770 | 0.93 | 0.93 | 1.00 | 0.26 | 11/11 | 0.67 | 36 |
| 0.780 | 0.64 | 0.72 | 0.69 | 0.24 | 11/11 | 0.73 | 17 |
| 0.790 | 0.48 | 0.52 | 0.31 | 0.28 | 9/11 | 0.93 | 4 |
| **0.800** (default) | 0.40 | 0.45 | 0.25 | 0.39 | 8/11 | 0.93 | 3 |
| 0.810 | 0.33 | 0.34 | 0.19 | 0.61 | 6/11 | 0.93 | 1 |
| 0.820 | 0.24 | 0.24 | 0.12 | 0.67 | 5/11 | 1.00 | 0 |

Выбран `0.80`: 0 FP на 14 калибровочных negative (максимум 0.789), recall 0.40. Добавленный после калибровки «задержание кометы телескопом» пробивает порог (0.815, слово «задержание») — известное ограничение cosine-порога. Margin относительно фоновых off-topic документов проверен и не лучше.

## Ошибки

| Ситуация | Результат workflow |
|---|---|
| нет `QDRANT_URL` при `semantic_query` | `failed` / `semantic_retrieval_not_configured` (HTTP 503) |
| Qdrant недоступен, коллекции нет, модель не загружается, CUDA OOM, размер вектора не совпадает | `failed` / `semantic_retrieval_unavailable` (HTTP 503) |
| кандидаты найдены, но ни один не прошёл порог (или 0 кандидатов) | `completed`, 0 результатов, «В текущем индексе не найдено сущностей с достаточной семантической релевантностью запросу…» |
| индекс построен другой моделью | `failed` / `semantic_retrieval_unavailable` (`IndexModelMismatchError`) |
| инкрементальная индексация после смены `SEMANTIC_VECTOR_BACKEND` без полного rebuild | `IndexBackendMismatchError` (CLI — код 2 и сообщение) |
| запрос без `semantic_query` при недоступном Qdrant | работает как раньше |
| некорректные semantic-переменные (`SEMANTIC_CANDIDATE_POOL_SIZE=abc`, `EMBEDDING_DEVICE=gpu`, `SEMANTIC_DENSE_MIN_SCORE=abc`, другая `EMBEDDING_MODEL_ID` без порога) | `SemanticConfigurationError` при сборке workflow: API 503 на **все** `/research/query`, включая structured-запросы без `semantic_query` (fail-fast); CLI — сообщение и выход |

## Конфигурация

| Переменная | По умолчанию |
|---|---|
| `SEMANTIC_VECTOR_BACKEND` | `qdrant`; `pgvector` — векторы в PostgreSQL, `QDRANT_URL` не нужна |
| `QDRANT_URL` | не задана (при `qdrant` semantic retrieval выключен) |
| `PERSON_QDRANT_COLLECTION` / `EVENT_QDRANT_COLLECTION` | `persons_semantic` / `events_semantic` (имена логических коллекций для обоих backend'ов) |
| `EMBEDDING_MODEL_ID` / `EMBEDDING_DEVICE` / `EMBEDDING_BATCH_SIZE` | `intfloat/multilingual-e5-base` / `auto` / `32` |
| `RERANKER_MODEL_ID` / `RERANKER_DEVICE` | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` / `auto` |
| `SEMANTIC_RERANK` | выключен |
| `SEMANTIC_CANDIDATE_POOL_SIZE` | `100` (1..200) |
| `SEMANTIC_DENSE_MIN_SCORE` | `0.80` для `intfloat/multilingual-e5-base`; для другой модели обязателен |
| `EVALUATION_DATABASE_URL` | для `evaluate-retrieval` |

## Ограничения

- Принять можно только кандидата из dense top-`SEMANTIC_CANDIDATE_POOL_SIZE`: lexical-hit вне dense-выдачи не имеет dense similarity и отклоняется (важно, если выше порога больше сущностей, чем размер пула).
- Порог откалиброван на маленьком синтетическом корпусе; при 0.80 теряется ~60% релевантных сущностей корпуса, часть нерелевантных внутри тематики всё равно принимается, word-play негативы («задержание кометы») пробивают порог.
- Индекс не обновляется автоматически после ingestion/классификации — `rebuild-semantic-index --incremental`.
- Нет межпроцессной блокировки индексации: не запускайте полный rebuild параллельно с другим rebuild/index — он пересоздаёт коллекцию.
- Загрузка моделей защищена lock'ом: параллельные первые запросы в FastAPI загружают модель один раз.
- Event retrieval доступен в `semantic-search`/evaluation; research workflow ищет только Person.
- Кандидаты из Qdrant могут ссылаться на удалённых/слитых persons — `ResearchService` их отбрасывает (только active).
- Semantic similarity не является identity evidence: ER v2 может использовать dense Person-кандидатов только для генерации кандидатов (`ER_SEMANTIC_CANDIDATES=1`, свой порог `ER_SEMANTIC_CANDIDATE_MIN_SCORE`, не `SEMANTIC_DENSE_MIN_SCORE`); score и решение от similarity не зависят ([Entity-Resolution](Entity-Resolution.md)).
- После link/create в ER v2 semantic document Person меняется — `rebuild-semantic-index --entity person --incremental`.

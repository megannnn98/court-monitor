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
RRF --> Service : candidate ids
Rerank --> Service : candidate ids
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

Только данные, связанные с сущностью; фрагмент — span события (≤ 400 символов), не статья. Смена формата → увеличить `PERSON_REPRESENTATION_VERSION`/`EVENT_REPRESENTATION_VERSION`.

## Модули (`src/semantic_retrieval/`)

| Модуль | Роль |
|---|---|
| `models.py` | `RetrievalQuery`, `RetrievalHit`, `RetrievalResult`, `SemanticDocument`, `RetrievalBackend`, ошибки |
| `documents.py` | `PersonSemanticDocumentBuilder`, `EventSemanticDocumentBuilder`, `compute_content_hash` |
| `document_store.py` | `SqlAlchemySemanticDocumentRepository`, `PostgresLexicalEntityRetriever` |
| `vector_store.py` | `VectorStore`, `QdrantVectorStore`, `point_id` |
| `embeddings.py` | `TextEmbedder`, `SentenceTransformerEmbedder` (E5, device) |
| `reranking.py` | `Reranker`, `CrossEncoderReranker` |
| `retrievers.py` | `EntityRetriever`, `QdrantEntityRetriever`, `HybridEntityRetriever`, `RerankingEntityRetriever` |
| `rrf.py` | `reciprocal_rank_fusion` |
| `indexer.py` | `SemanticIndexer` (rebuild / incremental / delete) |
| `metrics.py`, `evaluation.py` | MRR, Recall@k, Precision@k, nDCG@k; корпус и сравнение backend'ов |
| `factory.py` | конфигурация из env, сборка компонентов |

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

`evaluate-retrieval` очищает таблицы указанной БД и отказывается работать с базой, чьё имя не оканчивается на `_test`/`_eval`.

## Результаты evaluation (k = 5, реальные модели, 2026-09-14)

| backend | MRR | Recall@5 | nDCG@5 | P@5 | MRR (semantic-only) | Recall@5 (semantic-only) | nDCG@5 (semantic-only) |
|---|---|---|---|---|---|---|---|
| lexical | 0.545 | 0.371 | 0.408 | 0.218 | 0.000 | 0.000 | 0.000 |
| dense | 0.920 | 0.762 | 0.823 | 0.509 | 0.825 | 0.700 | 0.755 |
| hybrid | 0.920 | 0.780 | 0.851 | 0.527 | 0.825 | 0.700 | 0.755 |
| hybrid_reranked | 0.875 | 0.632 | 0.650 | 0.418 | 0.825 | 0.500 | 0.483 |

18 persons, 18 events, 11 запросов (5 semantic-only без лексического пересечения — это проверяется тестом). Reranker на этом корпусе ухудшает качество, поэтому по умолчанию выключен. Корпус маленький и синтетический.

## Ошибки

| Ситуация | Результат workflow |
|---|---|
| нет `QDRANT_URL` при `semantic_query` | `failed` / `semantic_retrieval_not_configured` (HTTP 503) |
| Qdrant недоступен, коллекции нет, модель не загружается, CUDA OOM, размер вектора не совпадает | `failed` / `semantic_retrieval_unavailable` (HTTP 503) |
| 0 кандидатов | `completed`, «Среди 0 кандидатов семантического поиска найдено 0…» |
| запрос без `semantic_query` при недоступном Qdrant | работает как раньше |
| некорректные semantic-переменные (`SEMANTIC_CANDIDATE_POOL_SIZE=abc`, `EMBEDDING_DEVICE=gpu`) | `SemanticConfigurationError`: API 503, CLI — сообщение и выход |

## Конфигурация

| Переменная | По умолчанию |
|---|---|
| `QDRANT_URL` | не задана (semantic retrieval выключен) |
| `PERSON_QDRANT_COLLECTION` / `EVENT_QDRANT_COLLECTION` | `persons_semantic` / `events_semantic` |
| `EMBEDDING_MODEL_ID` / `EMBEDDING_DEVICE` / `EMBEDDING_BATCH_SIZE` | `intfloat/multilingual-e5-base` / `auto` / `32` |
| `RERANKER_MODEL_ID` / `RERANKER_DEVICE` | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` / `auto` |
| `SEMANTIC_RERANK` | выключен |
| `SEMANTIC_CANDIDATE_POOL_SIZE` | `100` (1..200) |
| `EVALUATION_DATABASE_URL` | для `evaluate-retrieval` |

## Ограничения

- Нет порога релевантности: пул — top-N ближайших; результат означает «подходят под критерии среди N самых похожих».
- Индекс не обновляется автоматически после ingestion/классификации — `rebuild-semantic-index --incremental`.
- Нет межпроцессной блокировки индексации: не запускайте полный rebuild параллельно с другим rebuild/index — он пересоздаёт коллекцию.
- Загрузка моделей защищена lock'ом: параллельные первые запросы в FastAPI загружают модель один раз.
- Event retrieval доступен в `semantic-search`/evaluation; research workflow ищет только Person.
- Кандидаты из Qdrant могут ссылаться на удалённых/слитых persons — `ResearchService` их отбрасывает (только active).
- Semantic similarity не используется для entity resolution (ER v2 — отдельная часть).

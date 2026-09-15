# Testing & Quality

## Тесты (`tests/`)

Тесты разложены по тем же пакетам, что и `src/` (`tests/sources/`, `tests/extraction/`, `tests/persons/`, `tests/persecution/`, `tests/rosfinmonitoring/`, `tests/candidates/`, `tests/search/`, `tests/llm/`, `tests/research/`, `tests/semantic_retrieval/`, `tests/db/`); сквозные тесты CLI/API/end-to-end — в `tests/app/`; общие фейки и фикстуры БД — в `tests/support/` (`tests` добавлен в `pythonpath`). По одному файлу на модуль, `test_<module>.py`; например, для source layer: `article_parser`, `evaluation_corpus` (+`_persistence`), `evaluation_metrics`, `evaluation_models`, `postgres_lexical_search`, `search_evaluator`, `search_models`, `sqlalchemy_persistence`, а также source layer (этап 9): `source_adapter`, `source_ingestion`, `source_registry`, `discover_and_ingest` (CLI), `ovd_info_reference`, `ovd_info_listing_parser`, `ovd_info_source_adapter`, `retrying_fetcher`, `website_adapter`, `sota_vision_reference`, `sota_vision_listing_parser`, `sota_vision_article_parser`, `sota_vision_source_adapter`. `test_source_layer_integration.py` прогоняет полную цепочку discover → fetch → parse → persist для обоих источников без реальной сети (`httpx.MockTransport` + fake fetcher).

`chunker`, `dense_config`, `qdrant_chunk_indexer`, `qdrant_dense_search`, `text_embedder`, `hybrid_search`, `reranking_search`, `rrf`, `cross_encoder_reranker`, `reranker`, `search_factory` и их тесты удалены вместе с `ArticleChunk` и dense/hybrid поиском — см. [ADR 0002](../adr/0002-drop-dense-hybrid-search.md).

Chunk-уровневые dense/hybrid модули не вернулись; entity-level semantic retrieval (ADR 0011) покрыт тестами `test_semantic_*.py`, `test_retrieval_metrics.py`, `test_entity_retrieval_evaluation.py`, `test_research_graph_semantic.py`:

- unit — `QdrantClient(":memory:")`, `HashingEmbedder`, `KeywordReranker` (`tests/support/semantic_fakes.py`), подменённый модуль `sentence_transformers`; без Docker, GPU и моделей;
- `-m qdrant` — реальный Qdrant (`QDRANT_TEST_URL`), уникальные коллекции удаляются после теста;
- `-m semantic_models` — реальные E5 и cross-encoder (`SEMANTIC_MODEL_TESTS=1`, группа `semantic`).

`tests/conftest.py` — фикстура `test_engine` на `TEST_DATABASE_URL` (только база `court_monitor_test`), `TRUNCATE ... RESTART IDENTITY CASCADE` по всем таблицам pipeline (`db.maintenance.DISPOSABLE_TABLES`) между тестами (реальная Postgres, не мок).

`tests/fixtures/` — `evaluation_corpus.json`, `evaluation_cases.json` (см. [Evaluation](Evaluation.md)), `entity_retrieval_corpus.json`, `entity_retrieval_cases.json` (см. [Semantic-Retrieval](Semantic-Retrieval.md)), `ovd_info_listing.html`/`ovd_info_article.html`, `sota_vision_listing.html`/`sota_vision_article.html` (реальные HTML-страницы, скачанные напрямую с сайтов, для тестов listing/article-парсеров).

```bash
pytest
```

## Линтеры и типы

- **ruff** (`pyproject.toml`: `target-version = "py313"`, `line-length = 100`) — линт + форматирование.
- **mypy** — статическая типизация (конфиг не задан явно в `pyproject.toml`, дефолтный `mypy .`).

```bash
ruff check .
ruff format .
mypy .
```

## pre-commit (`.pre-commit-config.yaml`)

- `pre-commit-hooks`: `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-toml`, `check-added-large-files`, `check-merge-conflict`
- `ruff-check --fix`, `ruff-format`

```bash
pre-commit run --all-files
```

## Baseline-отчёты как регрессионный сигнал

`reports/postgres_lexical_baseline.json` — не часть тестового набора (не проверяется автоматически), а зафиксированный результат `evaluate-search` на момент коммита. Расхождение с ним при следующем прогоне — сигнал деградации поиска или намеренного изменения корпуса, требует явной проверки, не CI-гейт. Прежний baseline был на chunk-based corpus и удалён вместе с `ArticleChunk` — см. [Evaluation](Evaluation.md#baseline).

## Real-World Validation

Real-world validation не входит в обычный `pytest`: он требует disposable PostgreSQL, matching raw cache и golden dataset. См. [Real-World Validation](RealWorldValidation.md).

Быстрые проверки схемы/CLI:

```bash
uv run python src/main.py real-world-corpus-status
uv run python src/main.py real-world-golden validate
```

Полный run:

```bash
export EVALUATION_DATABASE_URL=postgresql+psycopg://court_monitor:court_monitor_dev@localhost:5433/court_monitor_eval
uv run python src/main.py evaluate-real-world --split dev --no-fail-on-gates
```

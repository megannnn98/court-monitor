# Testing & Quality

## Тесты (`tests/`)

По одному файлу на модуль `src/`, суффикс `test_<module>.py` — покрыты все модули: `article_parser`, `evaluation_corpus` (+`_persistence`), `evaluation_metrics`, `evaluation_models`, `postgres_lexical_search`, `search_evaluator`, `search_models`, `sqlalchemy_persistence`, а также source layer (этап 9): `source_adapter`, `source_ingestion`, `source_registry`, `discover_and_ingest` (CLI), `ovd_info_reference`, `ovd_info_listing_parser`, `ovd_info_source_adapter`, `retrying_fetcher`, `website_adapter`, `sota_vision_reference`, `sota_vision_listing_parser`, `sota_vision_article_parser`, `sota_vision_source_adapter`. `test_source_layer_integration.py` прогоняет полную цепочку discover → fetch → parse → persist для обоих источников без реальной сети (`httpx.MockTransport` + fake fetcher).

`chunker`, `dense_config`, `qdrant_chunk_indexer`, `qdrant_dense_search`, `text_embedder`, `hybrid_search`, `reranking_search`, `rrf`, `cross_encoder_reranker`, `reranker`, `search_factory` и их тесты удалены вместе с `ArticleChunk` и dense/hybrid поиском — см. [ADR 0002](../adr/0002-drop-dense-hybrid-search.md).

`tests/conftest.py` — фикстура `test_engine` на `TEST_DATABASE_URL`, `TRUNCATE ... RESTART IDENTITY CASCADE` по 3 таблицам (`parsed_articles`, `source_documents`, `sources`) между тестами (реальная Postgres, не мок).

`tests/fixtures/` — `evaluation_corpus.json`, `evaluation_cases.json` (см. [Evaluation](Evaluation.md)), `ovd_info_listing.html`/`ovd_info_article.html`, `sota_vision_listing.html`/`sota_vision_article.html` (реальные HTML-страницы, скачанные напрямую с сайтов, для тестов listing/article-парсеров).

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

# Testing & Quality

## Тесты (`tests/`)

По одному файлу на модуль `src/`, суффикс `test_<module>.py` — покрыты все модули: `article_parser`, `chunker`, `dense_config`, `evaluation_corpus` (+`_persistence`), `evaluation_metrics`, `evaluation_models`, `postgres_lexical_search`, `qdrant_chunk_indexer`, `qdrant_dense_search`, `search_evaluator`, `search_models`, `sqlalchemy_persistence`, `text_embedder`.

`tests/conftest.py` — фикстура `test_engine` на `TEST_DATABASE_URL`, `TRUNCATE ... RESTART IDENTITY CASCADE` по всем 4 таблицам между тестами (реальная Postgres, не мок).

`tests/fixtures/` — `evaluation_corpus.json`, `evaluation_cases.json` (см. [Evaluation](Evaluation.md)), `ovd_info_article.html` (реальная HTML-страница для теста `article_parser`).

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

`reports/postgres_lexical_baseline.json` и `reports/qdrant_dense_baseline.json` — не часть тестового набора (не проверяются автоматически), а зафиксированный результат `evaluate-search` на момент коммита. Расхождение с ними при следующем прогоне — сигнал деградации поиска или намеренного изменения корпуса/модели, требует явной проверки, не CI-гейт.

# Testing & Quality

## Зачем это нужно

Эта страница объясняет, какие проверки запускать перед изменениями и что они
реально доказывают. Оператору важны smoke-команды. Программисту — разница между
unit, PostgreSQL/Qdrant integration, model tests и real-world validation.

## Быстрый сценарий

Без внешних сервисов:

```bash
uv sync --frozen
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
uv run mypy --strict src tests
uv run pytest
```

С PostgreSQL и Qdrant:

```bash
docker compose up -d postgres
docker compose --profile semantic up -d qdrant
export TEST_DATABASE_URL=postgresql+psycopg://court_monitor:court_monitor_dev@localhost:5433/court_monitor_test
export QDRANT_TEST_URL=http://127.0.0.1:6333
DATABASE_URL="$TEST_DATABASE_URL" uv run alembic upgrade head
env -u DATABASE_URL uv run pytest
```

База для integration tests должна называться `court_monitor_test`.

## Что покрыто

Тесты разложены по тем же пакетам, что и `src/`:

- `tests/sources/` — source adapters, parsers, ingestion, retry;
- `tests/extraction/` — mentions, normalization, events, persistence;
- `tests/persons/` — ER v2, review queue, AI review policy;
- `tests/persecution/`, `tests/rosfinmonitoring/`, `tests/candidates/` —
  product decision layers;
- `tests/research/`, `tests/semantic_retrieval/`, `tests/llm/` — research,
  semantic retrieval, LLM adapters;
- `tests/app/` — CLI/API/end-to-end contracts;
- `tests/support/` — fakes and database fixtures.

`tests/conftest.py` даёт `test_engine` на `TEST_DATABASE_URL` и очищает pipeline
таблицы через `TRUNCATE ... RESTART IDENTITY CASCADE`.

## Пример результата

```text
1863 passed, 33 skipped
```

Смысл: обычные и integration tests прошли в этой среде; skipped обычно означают
отключенные реальные модели или live LLM, а не ошибку.

## Линтеры и типы

```bash
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
uv run mypy --strict src tests
```

`ruff` отвечает за lint + format. `mypy --strict` нужен для `src` и `tests`.

## Опциональные проверки

Live Together AI:

```bash
TOGETHER_LIVE_TESTS=1 TOGETHER_API_KEY=... TOGETHER_MODEL=... uv run pytest -m live_together
```

Semantic models:

```bash
uv sync --group semantic
SEMANTIC_MODEL_TESTS=1 uv run pytest -m semantic_models
```

Real Qdrant:

```bash
QDRANT_TEST_URL=http://127.0.0.1:6333 uv run pytest -m qdrant
```

Real-world validation:

```bash
uv run python src/main.py real-world-corpus-status
uv run python src/main.py real-world-golden validate
EVALUATION_DATABASE_URL=postgresql+psycopg://court_monitor:court_monitor_dev@localhost:5433/court_monitor_eval \
  uv run python src/main.py evaluate-real-world --split dev --no-fail-on-gates
```

## Данные и артефакты

- Fixtures: `tests/fixtures/`.
- Baseline reports: `reports/`.
- Real-world corpus and golden data: `evaluation/real_world/`, local cache
  `var/real_world/`.
- CI: `.github/workflows/ci.yml`.

## Ограничения и типичные ошибки

- Unit tests без PostgreSQL не проверяют миграции, транзакции, индексы,
  collation, upserts и concurrency.
- DB-тесты намеренно требуют базу `court_monitor_test`, чтобы не писать в
  рабочую БД.
- Qdrant/model/live LLM tests запускаются только явным opt-in.
- Real-world validation не является частью обычного `pytest`: ему нужны
  disposable DB, cache и golden dataset.
- Chunk-level dense/hybrid tests удалены вместе со старым `ArticleChunk`; текущий
  semantic retrieval — entity-level.

# Testing & Quality

## Зачем это нужно

Эта страница объясняет, какие проверки запускать перед изменениями и что они
реально доказывают. Оператору важны smoke-команды. Программисту — разница между
unit, PostgreSQL integration, model tests и real-world validation.

## Быстрый сценарий

Без внешних сервисов:

```bash
uv sync --frozen
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
uv run mypy --strict src tests
uv run pytest
```

С PostgreSQL:

```bash
# Один раз: отдельный кластер только для тестов, порт 5434. Пароль случайный,
# его хранит только контейнер.
docker run -d --name ebnv-pgvector-test -p 127.0.0.1:5434:5432 \
  -e POSTGRES_USER=court_monitor -e POSTGRES_DB=court_monitor_test \
  -e POSTGRES_PASSWORD="$(openssl rand -hex 16)" \
  pgvector/pgvector:pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a

# Каждый раз:
docker start ebnv-pgvector-test
TEST_PASSWORD=$(docker inspect ebnv-pgvector-test \
  --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^POSTGRES_PASSWORD=//p')
export TEST_DATABASE_URL="postgresql+psycopg://court_monitor:${TEST_PASSWORD}@127.0.0.1:5434/court_monitor_test"
DATABASE_URL="$TEST_DATABASE_URL" uv run alembic upgrade head
env -u DATABASE_URL uv run pytest
```

Не направляйте тесты на `5433`: это кластер `docker compose` с рабочей базой, а тесты
очищают таблицы (`TRUNCATE`). Имя базы `court_monitor_test` проверяет `tests/conftest.py`,
порт — никто.

База для integration tests должна называться `court_monitor_test`.

Не запускайте `pytest` фоном через `&` (в том числе `nohup … &`): неинтерактивный shell
отключает фоновым командам SIGINT, тесты это наследуют, и
`test_the_real_process_runner_streams_output_and_interrupts_on_stop` падает через 30 секунд с
кодом `-9` вместо `130`. Долгий прогон запускайте на переднем плане, вывод перенаправляйте
в файл (`uv run pytest > pytest.log 2>&1`).

## Что покрыто

Тесты разложены по тем же пакетам, что и `src/`:

- `tests/sources/` — source adapters, parsers, ingestion, retry;
- `tests/extraction/` — mentions, normalization, events, persistence;
- `tests/persons/` — ER v2, review queue, AI review policy;
- `tests/persecution/`, `tests/rosfinmonitoring/`, `tests/candidates/` —
  product decision layers;
- `tests/entities/` — шаги 3–5 консоли и безымянные фигуранты;
- `tests/monitoring/` — monitoring pipeline, отсев мусора и очистка;
- `tests/llm/` — LLM adapters;
- `tests/app/` — CLI/API/end-to-end contracts;
- `tests/support/` — fakes and database fixtures.

`tests/conftest.py` даёт `test_engine` на `TEST_DATABASE_URL` и очищает pipeline
таблицы через `TRUNCATE ... RESTART IDENTITY CASCADE`.

## Пример результата

```text
1863 passed, 33 skipped
```

Смысл: обычные и integration tests прошли в этой среде; skipped обычно означают
отключенную реальную модель распознавания имён, а не ошибку.

## Линтеры и типы

```bash
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
uv run mypy --strict src tests
```

`ruff` отвечает за lint + format. `mypy --strict` нужен для `src` и `tests`.

## Опциональные проверки

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
- Тесты с реальной моделью распознавания имён запускаются только явным opt-in.
- Real-world validation не является частью обычного `pytest`: ему нужны
  disposable DB, cache и golden dataset.

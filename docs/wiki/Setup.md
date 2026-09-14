# Setup & Run

## Переменные окружения

Основные переменные:

| Переменная | Использование |
|---|---|
| `DATABASE_URL` | PostgreSQL connection URL |
| `TOGETHER_API_KEY` | ключ Together AI для natural-language запросов (`ask`, `POST /research/query`) |
| `TOGETHER_MODEL` | id модели Together с поддержкой JSON schema; значения по умолчанию нет |
| `TOGETHER_TIMEOUT_SECONDS` | таймаут запроса к Together, по умолчанию `30` |

Для Docker Compose также используются:

```text
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD
```

Актуальный набор переменных для локального запуска (шаблон — `.env.example`, скопируйте его в `.env`):

```text
POSTGRES_DB=court_monitor
POSTGRES_USER=court_monitor
POSTGRES_PASSWORD=court_monitor_dev

DATABASE_URL=postgresql+psycopg://court_monitor:court_monitor_dev@localhost:5433/court_monitor

TOGETHER_API_KEY=
TOGETHER_MODEL=
TOGETHER_TIMEOUT_SECONDS=30
```

`TOGETHER_*` нужны только для natural-language запросов; без них `ask` и `POST /research/query` завершаются ошибкой конфигурации, остальной pipeline работает.

Если `.env` загружается через shell:

```bash
set -a
source .env
set +a
```

`set -a` нужен, чтобы переменные из `.env` экспортировались в окружение дочернего Python-процесса.

Переменные `QDRANT_URL`, `QDRANT_COLLECTION`, `QDRANT_EVALUATION_COLLECTION`, `EMBEDDING_MODEL_ID`, `RERANKER_MODEL_ID` использовались dense/hybrid/reranked-hybrid поиском — удалены вместе с ним, см. [ADR 0002](../adr/0002-drop-dense-hybrid-search.md); текущий код их не читает.

## Инфраструктура

```bash
docker compose up -d
```

Поднимает только PostgreSQL (порт `5433` на хосте) — единственную инфраструктуру, которую использует текущий код.

Qdrant оставлен в `compose.yaml` как опциональный сервис под profile `semantic` (кодом сейчас не используется, volume `qdrant_data` сохранён):

```bash
docker compose --profile semantic up -d
```

## Тесты и CI

```bash
uv sync --frozen
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy --strict src tests
uv run pytest                      # DB-тесты пропускаются без TEST_DATABASE_URL
```

PostgreSQL integration suite (база обязательно `court_monitor_test`):

```bash
docker compose up -d postgres
export TEST_DATABASE_URL=postgresql+psycopg://court_monitor:court_monitor_dev@localhost:5433/court_monitor_test
DATABASE_URL="$TEST_DATABASE_URL" uv run alembic upgrade head
env -u DATABASE_URL uv run pytest
```

URL соответствует значениям из `.env.example`; при других учётных данных подставьте свои. База `court_monitor_test` должна существовать (например, `docker compose exec postgres createdb -U court_monitor court_monitor_test`). Сокращение `TEST_DATABASE_URL="${DATABASE_URL%/*}/court_monitor_test"` работает только для URL без query-параметров (`?sslmode=…` будет отброшен).

Live-тест Together AI не запускается без явного opt-in:

```bash
TOGETHER_LIVE_TESTS=1 uv run pytest -m live_together
```

GitHub Actions (`.github/workflows/ci.yml`): `quality` (ruff, format, mypy), `tests` (pytest без БД), `integration` (PostgreSQL 18 service, `alembic upgrade head`, pytest с `TEST_DATABASE_URL`). Together AI в CI не вызывается.

## Миграции

```bash
alembic upgrade head
```

## Search

```bash
uv run python src/main.py search \
  "реабилитация нацизма" \
  --limit 5
```

Опции `--backend dense/hybrid/reranked-hybrid` больше нет — поиск только lexical.

## Discover and ingest

```bash
uv run python src/main.py discover-and-ingest --source ovd-info --limit 10
uv run python src/main.py discover-and-ingest --source sota-vision --limit 10
```

`--source` выбирает источник из `src/source_registry.py` (по умолчанию `ovd-info`). Обнаруженные ссылки на статьи проходят через общие `SourceIngestion`/`IngestionPipeline` — не сохраняются дважды при повторном запуске (dedup по `source_id` + `external_id`), ошибка одной статьи не прерывает остальные.

Разовая загрузка одной статьи ОВД-Инфо по прямому URL (не через discovery):

```bash
uv run python src/main.py ingest https://ovd.info/express-news/2026/09/01/some-article
```

## Evaluation

```bash
uv run python src/main.py evaluate-search \
  --output-path reports/postgres_lexical_baseline.json
```

Для evaluation рекомендуется отдельная PostgreSQL database, например:

```bash
DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5433/court_monitor_test" \
uv run python src/main.py evaluate-search \
  --output-path reports/postgres_lexical_baseline.json
```

Evaluation загружает фиксированный test corpus в PostgreSQL.

## Extraction

Extraction одной сохранённой статьи:

```bash
uv run python src/main.py extract-entities --article-id 123
```

Пакетный режим по источнику:

```bash
uv run python src/main.py extract-entities --source ovd-info --limit 100
uv run python src/main.py extract-entities --source sota-vision --limit 100
```

Команда печатает JSON-статистику:

```text
articles_processed
articles_skipped
articles_failed
mentions_created
events_created
```

Golden evaluation:

```bash
uv run python src/main.py evaluate-extraction \
  --corpus-path tests/fixtures/extraction_golden_corpus.json
```

Основной extractor deterministic, CPU-only, без LLM, внешних API и скачивания моделей.

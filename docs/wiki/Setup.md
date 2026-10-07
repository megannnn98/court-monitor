# Setup & Run

## Зачем это нужно

Эта страница — справочник по окружению: переменные, compose-профили, миграции,
тесты и запуск сервисов. Если нужен первый пошаговый прогон, начните с
[Getting Started](Getting-Started.md). Если нужен production-like выпуск, см.
[Rebuild-Image](Rebuild-Image.md).

## Быстрый сценарий

```bash
cp -n .env.example .env
set -a
source .env
set +a

docker compose up -d postgres
uv run alembic upgrade head
uv run python src/main.py validate-config
```

Ожидание: `validate-config` печатает настройки без секретов, Alembic показывает
актуальную схему, PostgreSQL доступен на `localhost:5433`.

## Переменные окружения

Основные переменные:

| Переменная | Использование |
|---|---|
| `DATABASE_URL` | PostgreSQL connection URL |
| `TOGETHER_API_KEY` | ключ Together AI для AI-review решений ER (`ENTITY_REVIEW_PROVIDER=together`, ADR 0020) |
| `TOGETHER_MODEL` | id модели Together с поддержкой JSON schema; значения по умолчанию нет |
| `TOGETHER_TIMEOUT_SECONDS` | таймаут запроса к Together, по умолчанию `30` |
| `TOGETHER_BASE_URL` | OpenAI-совместимый endpoint, по умолчанию `https://api.together.ai/v1`; локальная модель через Ollama — `http://127.0.0.1:11434/v1` |
| `JUNK_SCREEN` | отсев мусора перед шагом 2 ([Junk Screen](Junk-Screen.md)); в `compose.yaml` для API `1`, в коде по умолчанию выключен; нужен `uv sync --group semantic` |
| `EMBEDDING_DEVICE`, `EMBEDDING_BATCH_SIZE` | где и какими пачками считает модель отсева: `auto` (`cpu`/`cuda`), `32`; сама модель — та, что названа в `junk_screen_model.json` |
| `EVALUATION_DATABASE_URL` | одноразовая БД (`*_test`/`*_eval`) для `evaluate-er`, `evaluate-final` и `evaluate-real-world` |
| `ER_CANDIDATE_LIMIT` | кандидатов на упоминание в ER v2, по умолчанию `30` (2..200) |
| `ER_AUTO_LINK_MIN_SCORE`, `ER_REVIEW_MIN_SCORE`, `ER_MIN_MARGIN` | пороги решения ER v2, по умолчанию `0.85`, `0.40`, `0.10` (подобраны по `evaluate-er`) |
| `OPENROUTER_API_KEY` | ключ OpenRouter для модели шагов 3, 4, 5 и безымянных фигурантов; без него решают только правила, остальное «неясно» |
| `ENTITY_NORMALIZE_MODEL` | модель OpenRouter для этих шагов; по умолчанию `deepseek/deepseek-v4.1-flash` |
| `ASK_DAILY_BUDGET_USD` | сколько за день могут потратить вопросы страницы «Спросить» ([Ask](Ask.md)), по умолчанию `1` |
| `ENTITY_MODEL_BUDGET_USD` | сколько может потратить один запуск шага, по умолчанию `2`; что не спросили — спросит следующий запуск |
| `PIPELINE_SINCE` | рабочая дата (`YYYY-MM-DD`): шаг 2 удаляет публикации раньше неё; в `compose.yaml` — `2026-09-20`; пусто — по дате не удаляется |

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

EMBEDDING_DEVICE=auto
EMBEDDING_BATCH_SIZE=32

# Entity Resolution v2 (calibrated on tests/fixtures/er_v2_corpus.json)
ER_CANDIDATE_LIMIT=30
ER_AUTO_LINK_MIN_SCORE=0.85
ER_REVIEW_MIN_SCORE=0.40
ER_MIN_MARGIN=0.10
```

`TOGETHER_*` нужны только AI-review решений ER; без них решения, требующие проверки, идут человеку. Модель отсева мусора требует `uv sync --group semantic` ([Junk Screen](Junk-Screen.md)).

Если `.env` загружается через shell:

```bash
set -a
source .env
set +a
```

`set -a` нужен, чтобы переменные из `.env` экспортировались в окружение дочернего Python-процесса.

## Инфраструктура

```bash
docker compose up -d
```

Поднимает только PostgreSQL (порт `5433` на хосте).

Automated monitoring ([Monitoring](Monitoring.md), ADR 0013) — profile `monitoring` (Dagster webserver на `127.0.0.1:3000`, daemon, отдельная БД `DAGSTER_PG_DB`); перед запуском применить миграции:

```bash
uv run alembic upgrade head
docker compose --profile monitoring up -d --build
```

Переменные: `MONITORING_ENABLED_SOURCES`, `MONITORING_CRON`, `MONITORING_DISCOVERY_LIMIT`, `MONITORING_STALE_RUN_AFTER_MINUTES`, `DAGSTER_PG_DB` (см. `.env.example`).

### Production-like запуск

Пошаговый выпуск на этой машине (GPU-образ, builder, миграции, проверка, уборка места) — [Rebuild-Image](Rebuild-Image.md).

[ADR 0014](../adr/0014-production-deployment.md): один образ `court-monitor:local` для API, Dagster и миграций; профиль `production` = PostgreSQL + API + Dagster. Миграции — отдельный явный шаг, API и Dagster их не применяют:

```bash
docker compose --profile production build
docker compose up -d postgres
docker compose run --rm migrate
docker compose --profile production up -d

curl -s http://127.0.0.1:8001/health/live    # процесс отвечает
curl -s http://127.0.0.1:8001/health/ready   # 503 unavailable: БД недоступна или схема не на head
```

`/health/ready` возвращает `ready`, `degraded` (HTTP 200: stale monitoring run) или `unavailable` (HTTP 503). Healthcheck контейнера API проверяет только `/health/live`.

| Переменная | Использование |
|---|---|
| `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW` | пул на процесс (API worker, Dagster run), по умолчанию `5`, `10` |
| `DATABASE_POOL_TIMEOUT`, `DATABASE_CONNECT_TIMEOUT` | ожидание соединения из пула и подключения к PostgreSQL, секунды, `30`, `10` |

Все порты опубликованы только на `127.0.0.1` (PostgreSQL `5433`, API `8001`, Dagster `3000`). У API нет аутентификации и rate limiting — наружу только через reverse proxy с ними.

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

GitHub Actions (`.github/workflows/ci.yml`): `quality` (ruff, format, mypy), `tests` (pytest без БД), `integration` (PostgreSQL 18 service, `alembic upgrade head`, pytest с `TEST_DATABASE_URL`). Группа `semantic` в CI не ставится, модели не скачиваются, Together AI не вызывается.

## Миграции

```bash
alembic upgrade head
```

## Discover and ingest

```bash
uv run python src/main.py discover-and-ingest --source ovd-info --limit 10
uv run python src/main.py discover-and-ingest --source sota-vision --limit 10
```

`--source` выбирает источник из `src/sources/source_registry.py` (по умолчанию `ovd-info`). Обнаруженные ссылки на статьи проходят через общие `SourceIngestion`/`IngestionPipeline` — не сохраняются дважды при повторном запуске (dedup по `source_id` + `external_id`), ошибка одной статьи не прерывает остальные.

Разовая загрузка одной статьи ОВД-Инфо по прямому URL (не через discovery):

```bash
uv run python src/main.py ingest https://ovd.info/express-news/2026/09/01/some-article
```

## Evaluation

Real-world validation использует отдельный manifest/golden dataset/cache и disposable database:

```bash
uv run python src/main.py real-world-corpus-status
uv run python src/main.py real-world-golden validate

export EVALUATION_DATABASE_URL=postgresql+psycopg://court_monitor:court_monitor_dev@localhost:5433/court_monitor_eval
uv run python src/main.py evaluate-real-world --split dev --no-fail-on-gates
```

Детали: [Real-World Validation](RealWorldValidation.md).

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

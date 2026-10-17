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
uv run court-monitor validate-config
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
| `ENTITY_NORMALIZE_MODEL` | модель OpenRouter для этих шагов, пока в консоли не выбрана другая ([Entity Model](Entity-Model.md)); по умолчанию `deepseek/deepseek-v4.1-flash` |
| `ENTITY_DECISION_MODEL` | модель решений (фигуранты, политичность, новости), пока в консоли не выбрана другая; по умолчанию — `ENTITY_NORMALIZE_MODEL`, иначе DeepSeek |
| `ASK_DAILY_BUDGET_USD` | сколько за день могут потратить вопросы страницы «Спросить» ([Ask](Ask.md)), по умолчанию `1` |
| `ENTITY_MODEL_BUDGET_USD` | сколько может потратить один запуск шага, по умолчанию `2`; что не спросили — спросит следующий запуск |
| `INVESTIGATION_MAX_LLM_CALLS`, `INVESTIGATION_MAX_FOLLOWUPS`, `INVESTIGATION_MAX_EVIDENCE_ITEMS` | потолки одного вопроса `investigate` ([ADR 0026](../adr/0026-investigation-workflow-on-langgraph.md)): обращений к модели `2`, дополнительных раундов поиска `2`, материалов в ответе `40`. Читаются из окружения процесса; `compose.yaml` их в контейнер не передаёт, там действуют значения по умолчанию |
| `INVESTIGATION_MAX_SEARCHES`, `INVESTIGATION_MAX_PARALLEL_SEARCHES`, `INVESTIGATION_TIMEOUT_SECONDS`, `INVESTIGATION_SEARCH_TIMEOUT_SECONDS` | поисков на вопрос `40`, одновременно `4`, на весь вопрос `120` с, на один поиск `20` с ([ADR 0027](../adr/0027-investigation-runtime-scenarios-and-retrieval.md)). Запрос может только уменьшить |
| `INVESTIGATION_TELEGRAM_CHANNELS` | каналы из `channels.csv`, чей публичный поиск спрашивает исследование, через запятую; по умолчанию `ovdinfolive,mediazzzona,pzk_memorial,politzekinfo`; `none` — без внешних источников |
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

`MONITORING_DISCOVERY_LIMIT` (по умолчанию 5000) — потолок, а не размер запуска:
обычный запуск листает источник от самых свежих публикаций назад и останавливается
на странице, где всё уже загружено. Telegram-каналы дополнительно ограничены окном в
30 дней. Запуск, которому не на чем остановиться — первая загрузка источника или
загрузка после оборванной, — читает не больше 500 публикаций; глубже — только явный
`--backfill`. При прежнем значении 50 канал, выпустивший между двумя запусками больше
50 постов, терял более старые навсегда: следующий запуск снова начинал с самых свежих.

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
| `ALLOWED_ORIGINS` | откуда браузеру можно слать изменяющие запросы (POST и др.), через запятую; заменяет список по умолчанию `http://127.0.0.1:8001,http://localhost:8001,http://127.0.0.1:5173,http://localhost:5173`. Свой адрес (заголовок `Host`) разрешён всегда, так что SSH-туннель на любом порту работает без настройки; reverse proxy, переписывающему `Host`, нужен здесь свой публичный адрес (ADR 0022) |

Все порты опубликованы только на `127.0.0.1` (PostgreSQL `5433`, API `8001`, Dagster `3000`). У API нет аутентификации и rate limiting — наружу только через reverse proxy с ними.

## Тесты и CI

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check packages apps tools tests scripts
uv run mypy --strict
uv run pytest                      # DB-тесты пропускаются без TEST_DATABASE_URL
```

PostgreSQL integration suite (база обязательно `court_monitor_test`):

```bash
# Один раз: отдельный кластер только для тестов, порт 5434. Пароль случайный,
# его хранит только контейнер.
docker run -d --name ebnv-postgres-test -p 127.0.0.1:5434:5432 \
  -e POSTGRES_USER=court_monitor -e POSTGRES_DB=court_monitor_test \
  -e POSTGRES_PASSWORD="$(openssl rand -hex 16)" \
  postgres:18.6-bookworm@sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c

# Каждый раз:
docker start ebnv-postgres-test
TEST_PASSWORD=$(docker inspect ebnv-postgres-test \
  --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^POSTGRES_PASSWORD=//p')
export TEST_DATABASE_URL="postgresql+psycopg://court_monitor:${TEST_PASSWORD}@127.0.0.1:5434/court_monitor_test"
DATABASE_URL="$TEST_DATABASE_URL" uv run alembic upgrade head
env -u DATABASE_URL uv run pytest
```

Не направляйте тесты на `5433`: это кластер `docker compose` с рабочей базой, а тесты
очищают таблицы (`TRUNCATE`). Имя базы `court_monitor_test` проверяет `tests/conftest.py`,
порт — никто.

GitHub Actions (`.github/workflows/ci.yml`): `quality` (ruff, format, mypy), `tests` (pytest без БД), `integration` (PostgreSQL 18 service, `alembic upgrade head`, pytest с `TEST_DATABASE_URL`). Группа `semantic` в CI не ставится, модели не скачиваются, Together AI не вызывается.

## Миграции

```bash
alembic upgrade head
```

## Discover and ingest

```bash
uv run court-monitor discover-and-ingest --source ovd-info --limit 10
uv run court-monitor discover-and-ingest --source sota-vision --limit 10
```

`--source` выбирает источник из `packages/court-ingestion/src/ingestion/sources/source_registry.py` (по умолчанию `ovd-info`). Обнаруженные ссылки на статьи проходят через общие `SourceIngestion`/`IngestionPipeline` — не сохраняются дважды при повторном запуске (dedup по `source_id` + `external_id`), ошибка одной статьи не прерывает остальные.

Разовая загрузка одной статьи ОВД-Инфо по прямому URL (не через discovery):

```bash
uv run court-monitor ingest https://ovd.info/express-news/2026/09/01/some-article
```

## Evaluation

Real-world validation использует отдельный manifest/golden dataset/cache и disposable database:

```bash
uv run court-evaluation real-world-corpus-status
uv run court-evaluation real-world-golden validate

export EVALUATION_DATABASE_URL=postgresql+psycopg://court_monitor:court_monitor_dev@localhost:5433/court_monitor_eval
uv run court-evaluation evaluate-real-world --split dev --no-fail-on-gates
```

Детали: [Real-World Validation](RealWorldValidation.md).

## Extraction

Extraction одной сохранённой статьи:

```bash
uv run court-monitor extract-entities --article-id 123
```

Пакетный режим по источнику:

```bash
uv run court-monitor extract-entities --source ovd-info --limit 100
uv run court-monitor extract-entities --source sota-vision --limit 100
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
uv run court-monitor evaluate-extraction \
  --corpus-path tests/fixtures/extraction_golden_corpus.json
```

Основной extractor deterministic, CPU-only, без LLM, внешних API и скачивания моделей.

# Setup & Run

## Переменные окружения

Основные переменные:

| Переменная | Использование |
|---|---|
| `DATABASE_URL` | PostgreSQL connection URL |

Для Docker Compose также используются:

```text
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD
```

Пример конфигурации находится в:

```text
.env.example
```

Если `.env` загружается через shell:

```bash
set -a
source .env
set +a
```

`set -a` нужен, чтобы переменные из `.env` экспортировались в окружение дочернего Python-процесса.

Переменные `QDRANT_URL`, `QDRANT_COLLECTION`, `QDRANT_EVALUATION_COLLECTION`, `EMBEDDING_MODEL_ID`, `RERANKER_MODEL_ID` использовались dense/hybrid/reranked-hybrid поиском — удалены вместе с ним, см. [ADR 0002](../adr/0002-drop-dense-hybrid-search.md). `compose.yaml` всё ещё поднимает Qdrant — сейчас он кодом не используется.

## Инфраструктура

Запуск:

```bash
docker compose up -d
```

Используется PostgreSQL.

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

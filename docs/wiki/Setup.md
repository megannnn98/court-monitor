# Setup & Run

## Переменные окружения

Обязательные (падают с `KeyError`/`RuntimeError`, если не заданы):

| Переменная | Использование |
|---|---|
| `DATABASE_URL` | `main.py` — строка подключения SQLAlchemy к Postgres, обязательна для всех команд |
| `QDRANT_URL` | адрес Qdrant, только для `--backend dense` |
| `QDRANT_COLLECTION` | имя коллекции Qdrant, только для `--backend dense` |
| `EMBEDDING_MODEL_ID` | ID модели `sentence-transformers`, только для `--backend dense` |

Для `docker compose` (`compose.yaml`) дополнительно: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`. Пример переменных — `.env.example` в корне репозитория (не читался этим агентом — файл в директории с ограничением доступа; смотри вручную).

## Инфраструктура (`compose.yaml`)

- **postgres** — `postgres:18.6-bookworm`, порт хоста `5433 → 5432`, volume `postgres_data`, healthcheck `pg_isready`.
- **qdrant** — `qdrant/qdrant:v1.19.0`, порт `127.0.0.1:6333` (только localhost), volume `qdrant_data`.

```bash
docker compose up -d
```

## Миграции (Alembic)

```bash
alembic upgrade head
```

Цепочка миграций (`migrations/versions/`):
1. `fdac899276a2_create_ingestion_tables` — 4 базовые таблицы
2. `df2c42a78b48_add_article_chunk_search_vector` → зависит от (1) — добавляет `search_vector` + GIN-индекс

Конфигурация — `alembic.ini` + `migrations/env.py`.

## CLI (`src/main.py`, `pythonpath = src`)

```bash
# загрузить и сохранить одну публикацию
python -m main ingest https://ovd.info/express-news/...

# lexical-поиск (по умолчанию)
python -m main search "реабилитация нацизма" --limit 5

# dense-поиск
python -m main search "реабилитация нацизма" --backend dense

# оценка качества поиска
python -m main evaluate-search --backend lexical
python -m main evaluate-search --backend dense --output-path reports/my_run.json
```

`evaluate-search` сам загружает тестовый корпус в БД перед прогоном (см. [Evaluation](Evaluation.md)) — предполагает БД, отдельную от продовых данных, либо готовность к побочным записям.

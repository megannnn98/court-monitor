# Setup & Run

## Переменные окружения

Основные переменные:

| Переменная | Использование |
|---|---|
| `DATABASE_URL` | PostgreSQL connection URL |
| `QDRANT_URL` | адрес Qdrant |
| `QDRANT_COLLECTION` | рабочая dense collection |
| `QDRANT_EVALUATION_COLLECTION` | отдельная collection для evaluation |
| `EMBEDDING_MODEL_ID` | sentence-transformers embedding model |
| `RERANKER_MODEL_ID` | sentence-transformers CrossEncoder model |

Пример reranker:

```env
RERANKER_MODEL_ID=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
```

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

## Инфраструктура

Запуск:

```bash
docker compose up -d
```

Используются:

```text
PostgreSQL
Qdrant
```

## Миграции

```bash
alembic upgrade head
```

## Dense Index

Обычный рабочий dense index перестраивается явно:

```bash
uv run python src/main.py rebuild-dense-index
```

Обычная команда `search` индекс автоматически не пересоздаёт.

## Search

Lexical:

```bash
uv run python src/main.py search \
  "реабилитация нацизма" \
  --backend lexical \
  --limit 5
```

Dense:

```bash
uv run python src/main.py search \
  "реабилитация нацизма" \
  --backend dense \
  --limit 5
```

Hybrid:

```bash
uv run python src/main.py search \
  "реабилитация нацизма" \
  --backend hybrid \
  --limit 5
```

Hybrid + cross-encoder reranking:

```bash
uv run python src/main.py search \
  "реабилитация нацизма" \
  --backend reranked-hybrid \
  --limit 5
```

## Evaluation

Lexical:

```bash
uv run python src/main.py evaluate-search \
  --backend lexical \
  --output-path reports/postgres_lexical_baseline.json
```

Dense:

```bash
uv run python src/main.py evaluate-search \
  --backend dense \
  --output-path reports/qdrant_dense_baseline.json
```

Hybrid:

```bash
uv run python src/main.py evaluate-search \
  --backend hybrid \
  --output-path reports/hybrid_baseline.json
```

Reranked hybrid:

```bash
uv run python src/main.py evaluate-search \
  --backend reranked-hybrid \
  --output-path reports/reranked_hybrid_baseline.json
```

Для evaluation рекомендуется отдельная PostgreSQL database, например:

```bash
DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5433/court_monitor_test" \
uv run python src/main.py evaluate-search \
  --backend reranked-hybrid \
  --output-path reports/reranked_hybrid_baseline.json
```

Evaluation загружает фиксированный test corpus в PostgreSQL.

Dense-based evaluation также пересоздаёт `QDRANT_EVALUATION_COLLECTION`.

Рабочая и evaluation Qdrant collections должны иметь разные имена.

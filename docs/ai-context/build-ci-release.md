# Сборка, CI, Docker

## Зависимости

Python ≥3.12, uv (пакетный менеджер). Основные зависимости: httpx, selectolax, pydantic v2, sqlalchemy 2, alembic, fastapi, typer, structlog, tenacity, pyyaml, dbfread.

Опциональные extras: `pg` (psycopg), `playwright`, `nlp` (spacy). Для `nlp` модель ставится отдельно — её нет в обычном индексе PyPI:

```bash
pip install -e ".[nlp]"
python -m spacy download ru_core_news_lg   # ~500 МБ
```

## Установка

```bash
make install       # uv sync + dev dependencies
```

## Тесты

```bash
make test          # все (336 тестов)
make test-unit     # только unit
make test-integration  # только integration
make lint          # ruff check + ruff format --check
make typecheck     # mypy
```

## Docker Compose

```bash
cp .env.example .env
make docker-up     # postgres + app (FastAPI на :8000)
# или
docker compose up --build
```

После старта: `GET http://localhost:8000/health`, `GET /docs`.

## CI (GitHub Actions)

Джобы:

| Джоб | Что делает |
|---|---|
| `test` | Матрица Python 3.12–3.14: ruff format/lint, mypy, pytest |
| `unit-tests` | Быстрый прогон только `tests/unit` |
| `changes` | `paths-filter`: определяет, затронуты ли файлы, влияющие на образ (включая `alembic.ini`) |
| `build-image` | Docker build — только если `changes` сказал «да» |
| `secrets` | gitleaks |

## Миграции

```bash
make init-db       # alembic upgrade head
make migrate       # alias
```

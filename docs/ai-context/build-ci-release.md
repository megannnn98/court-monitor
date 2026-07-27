# Сборка, CI, Docker

## Зависимости

Python ≥3.12, uv (пакетный менеджер). Основные зависимости: httpx, selectolax, pydantic v2, sqlalchemy 2, alembic, fastapi, typer, structlog, tenacity, pyyaml, dbfread.

## Установка

```bash
make install       # uv sync + dev dependencies
```

## Тесты

```bash
make test          # все (134 теста)
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

Матрица Python 3.12–3.14: ruff format/lint, mypy, pytest, docker build, gitleaks.

## Миграции

```bash
make init-db       # alembic upgrade head
make migrate       # alias
```

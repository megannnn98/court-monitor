# court-monitor

> **Подробный запуск шаг за шагом — [Getting Started](docs/wiki/Getting-Started.md)**: окружение, ручной pipeline, API, monitoring, проверки, частые проблемы.

Pipeline для поиска политически преследуемых людей, которых нет в перечне Росфинмониторинга.

```text
источники (ОВД-Инфо, SOTA, Telegram-каналы, пресс-службы судов) → статьи → extraction → canonical persons
→ классификация преследования → сопоставление с Росфинмониторингом → кандидаты
```

Extraction, entity resolution и классификация — детерминированные rule-based. Together AI используется только для опционального LLM review спорных решений entity resolution; применяет решения детерминированная policy, а без модели они остаются в human review.

## Быстрый старт

Требования: Python 3.13+, [uv](https://docs.astral.sh/uv/), Docker.

```bash
uv sync --frozen
cp .env.example .env
set -a; source .env; set +a

docker compose up -d
uv run alembic upgrade head

uv run court-monitor discover-and-ingest --source ovd-info --limit 20
uv run court-monitor extract-entities
uv run court-monitor resolve-people
uv run court-monitor classify-persecution
uv run court-monitor list-candidates --snapshot-id <id> --output-path candidates.json
```

`list-candidates` требует загруженный snapshot Росфинмониторинга — см. [Getting Started, шаги 6–7](docs/wiki/Getting-Started.md#6-optional-проверить-rosfinmonitoring-snapshots). Все команды: `uv run court-monitor --help`.

## Проверки

```bash
uv run ruff check .
uv run ruff format --check packages apps tools tests scripts migrations
uv run mypy --strict
uv run pytest    # для PostgreSQL-тестов нужен TEST_DATABASE_URL, см. docs/wiki/Testing.md
uv run python scripts/check_package_isolation.py   # каждый пакет workspace отдельно
```

## Структура

uv workspace: модули — отдельные устанавливаемые пакеты (`packages/court-*`), приложение
(composition root, CLI `court-monitor`, API, Dagster) — `apps/court-monitor`, evaluation —
`tools/court-evaluation` (CLI `court-evaluation`). Граф зависимостей и что чем владеет —
[docs/architecture/package-map.md](docs/architecture/package-map.md).

## Документация

- [Implementation Status](docs/wiki/Implementation-Status.md) — текущее состояние: компоненты, команды, результаты последней проверки, ограничения
- [docs/wiki](docs/wiki/Home.md) — вики: архитектура, pipeline, entity resolution, классификация, Росфинмониторинг, безымянные фигуранты, monitoring, evaluation
- [Local Web UI](docs/wiki/Local-Web-UI.md) — локальная веб-морда для ER-ревью и карточек с evidence spans
- [Package map](docs/architecture/package-map.md) — пакеты workspace, их зависимости и публичные API
- [docs/adr](docs/adr/) — архитектурные решения
- [CONTEXT.md](CONTEXT.md) — глоссарий доменных терминов

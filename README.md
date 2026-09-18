# court-monitor

> **Подробный запуск шаг за шагом — [Getting Started](docs/wiki/Getting-Started.md)**: окружение, ручной pipeline, API, monitoring, проверки, частые проблемы.

Pipeline для поиска политически преследуемых людей, которых нет в перечне Росфинмониторинга.

```text
источники (ОВД-Инфо, SOTA, Telegram-каналы, пресс-службы судов) → статьи → extraction → canonical persons
→ классификация преследования → сопоставление с Росфинмониторингом → кандидаты
```

Extraction, entity resolution и классификация — детерминированные rule-based. LLM (Together AI) используется только чтобы перевести вопрос на естественном языке в структурированный запрос; факты берутся из PostgreSQL. Спорные случаи уходят на human review, а не решаются автоматически.

## Быстрый старт

Требования: Python 3.13+, [uv](https://docs.astral.sh/uv/), Docker.

```bash
uv sync --frozen
cp .env.example .env
set -a; source .env; set +a

docker compose up -d
uv run alembic upgrade head

uv run python src/main.py discover-and-ingest --source ovd-info --limit 20
uv run python src/main.py extract-entities
uv run python src/main.py resolve-people
uv run python src/main.py classify-persecution
uv run python src/main.py list-candidates --snapshot-id <id> --output-path candidates.json
```

`list-candidates` требует загруженный snapshot Росфинмониторинга — см. [Getting Started, шаги 6–7](docs/wiki/Getting-Started.md#6-optional-проверить-rosfinmonitoring-snapshots). Все команды: `uv run python src/main.py --help`.

## Проверки

```bash
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
uv run mypy --strict src tests
uv run pytest    # для PostgreSQL-тестов нужен TEST_DATABASE_URL, см. docs/wiki/Testing.md
```

## Документация

- [Implementation Status](docs/wiki/Implementation-Status.md) — текущее состояние: компоненты, команды, результаты последней проверки, ограничения
- [docs/wiki](docs/wiki/Home.md) — вики: архитектура, pipeline, entity resolution, классификация, Росфинмониторинг, research, monitoring, evaluation
- [Local Web UI](docs/wiki/Local-Web-UI.md) — локальная веб-морда для ER-ревью и карточек с evidence spans
- [docs/adr](docs/adr/) — архитектурные решения
- [CONTEXT.md](CONTEXT.md) — глоссарий доменных терминов

# Architecture

## Зачем это нужно

Эта страница показывает границы системы: где доменная логика, где orchestration,
где хранилища и какие части можно восстановить из PostgreSQL. Оператору это
помогает понять, почему один сценарий запускается через monitoring, а другой
через CLI. Программисту — куда добавлять новую функцию, не смешивая слои.

## Быстрый сценарий

```bash
# одноразовый ручной прогон pipeline
uv run python src/main.py monitor --catch-up

# статус runs и findings
uv run python src/main.py monitoring-status
uv run python src/main.py monitoring-findings --all

# локальная web-консоль
docker compose --profile api up -d
```

CLI, FastAPI и Dagster собирают сервисы через один composition root, поэтому
доменная логика не должна зависеть от конкретного способа запуска.

## Что происходит внутри

```plantuml
@startuml
title court-monitor architecture

rectangle "Dagster\n(schedules, monitoring_job)" as Dagster #EEF
rectangle "CLI\nsrc/main.py" as CLI
rectangle "FastAPI\napi.py + web/" as API

rectangle "application.build_application_services()" as Root

package "Orchestration: src/monitoring" #EEF {
  component "MonitoringService" as Monitoring
  component "MonitoringFindingService" as Findings
}

package "Domain" {
  component "Sources\nSourceAdapter, IngestionPipeline" as Sources
  component "Extraction" as Extraction
  component "ER v2" as ER
  component "Persecution" as Persecution
  component "Rosfinmonitoring" as Rosfin
  component "Semantic index" as Semantic
  component "Research / Candidates" as Research
}

database "PostgreSQL\nsource of truth" as PG
database "Qdrant / pgvector\nderived index" as Vectors
database "PostgreSQL\nDagster metadata" as DagsterDB

Dagster --> Root
CLI --> Root
API --> Root
Root --> Monitoring
Monitoring --> Sources
Monitoring --> Extraction
Monitoring --> ER
Monitoring --> Persecution
Monitoring --> Rosfin
Monitoring --> Semantic
Monitoring --> Findings
Findings --> Research

Sources --> PG
Extraction --> PG
ER --> PG
Persecution --> PG
Rosfin --> PG
Research --> PG
Semantic --> PG
Semantic --> Vectors
Monitoring --> PG
Dagster --> DagsterDB
@enduml
```

## Кодовые точки входа

| Слой | Где | Ответственность |
|---|---|---|
| CLI | `src/main.py`, `src/cli/` | парсинг команд и вызов доменных сервисов |
| API / UI | `src/api.py`, `src/web/` | FastAPI, JSON endpoints, operator console |
| Composition root | `src/application.py`, `src/cli/context.py` | настройки, engine, session factory, сервисы |
| Orchestration | `src/monitoring/` | порядок стадий, runs, checkpoints, findings |
| Domain | `src/sources/`, `src/extraction/`, `src/persons/`, `src/persecution/`, `src/rosfinmonitoring/`, `src/candidates/`, `src/research/` | бизнес-решения и persistence |
| Semantic index | `src/semantic_retrieval/` | semantic documents и vector store |
| Database | `src/db/`, `migrations/` | ORM и Alembic schema |

## Данные и артефакты

- PostgreSQL — source of truth: статьи, mentions, persons, classifications,
  RF matches, monitoring runs.
- Qdrant или pgvector — производный индекс; его можно перестроить из PostgreSQL.
- Dagster metadata — отдельная БД для scheduler/webserver, не доменная история.
- Reports и evaluation artifacts лежат в `reports/` и `evaluation/`.

## Проверка

```bash
uv run python src/main.py validate-config
uv run python src/main.py --help
uv run pytest tests/monitoring tests/app
```

Для production-like запуска см. [Setup](Setup.md) и [Rebuild-Image](Rebuild-Image.md).

## Ограничения и типичные ошибки

- Dagster не принимает доменных решений; он только запускает pipeline.
- API и Dagster не применяют миграции сами. Миграции — явный шаг.
- Qdrant/pgvector не источник фактов. При сомнении сверяться с PostgreSQL.
- Порты compose опубликованы на localhost; наружный доступ требует reverse proxy
  с auth/rate limit.

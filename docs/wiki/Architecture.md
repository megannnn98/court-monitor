# Architecture

Компоненты court-monitor и их границы. Подробности этапов — на страницах [Overview](Overview.md), [Monitoring](Monitoring.md) и в [ADR](../adr/).

- **Домен** (`sources`, `extraction`, `persons`, `persecution`, `rosfinmonitoring`, `candidates`, `research`, `semantic_retrieval`) принимает решения: что за документ, кто этот человек, преследование ли это, есть ли он в списке РФМ.
- **Orchestration** (`monitoring`, Dagster) только выбирает работу, вызывает доменные сервисы и ведёт учёт run'ов (ADR 0013).
- **PostgreSQL** — source of truth. **Qdrant** — производный индекс (ADR 0011), восстанавливается из PostgreSQL. **Метаданные Dagster** — в отдельной БД.
- **Composition root** `application.build_application_services()` собирает граф объектов для monitoring CLI, monitoring API и Dagster resource.
- **Deployment** (ADR 0014): один образ для API, Dagster и миграций; compose-профиль `production`; миграции — явный шаг `migrate`; liveness (`/health/live`) отделён от readiness (`/health/ready`); порты только на localhost.

```plantuml
@startuml
title court-monitor architecture

rectangle "Dagster\n(schedules, monitoring_job, monitoring_derived_job)" as Dagster #EEF
rectangle "CLI (main.py)" as CLI
rectangle "FastAPI (web/, entry point api.py)" as API

rectangle "application.build_application_services()" as Root

package "Orchestration: src/monitoring" #EEF {
  component "MonitoringService" as Monitoring
  component "MonitoringFindingService" as Findings
}

package "Domain" {
  component "Sources\n(SourceAdapter, IngestionPipeline)" as Sources
  component "Extraction" as Extraction
  component "ER v2" as ER
  component "Persecution" as Persecution
  component "Rosfinmonitoring" as Rosfin
  component "Semantic index" as Semantic
  component "Research / CandidateQueryService" as Research
}

database "PostgreSQL\ncourt_monitor" as PG
database "Qdrant" as Qdrant
database "PostgreSQL\ncourt_monitor_dagster" as DagsterDB

Dagster --> Root
CLI --> Root
API --> Root
CLI --> Research
API --> Research
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
Semantic --> Qdrant
Monitoring --> PG
Dagster --> DagsterDB
@enduml
```

Pipeline монитора: Sources → Discovery → Ingestion → Extraction → ER v2 → Persecution → Rosfin → Semantic Index → Research/Candidate query → Monitoring Finding; Dagster запускает его снаружи и в доменные решения не входит.

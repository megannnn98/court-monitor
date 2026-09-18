# court-monitor — вики

`court-monitor` — восстановленный первый вертикальный срез проекта (случайная потеря файлов, см. `README.md`). Универсальный source layer (этап 9): `SourceAdapter.discover()` находит статьи на сайте источника → `fetch()` загружает → `ArticleParser.parse()` разбирает целиком (без chunking) → сохранение в PostgreSQL → lexical-поиск → оценка качества поиска. Этап 10 добавляет extraction поверх полного текста: упоминания сущностей, правовые ссылки и базовые события. Два источника на одной архитектуре: ОВД-Инфо и SOTA (sota.vision).

Эта вики покрывает **только код, реально присутствующий в репозитории на момент написания**. Более ранний функционал (сопоставление ФИО с реестром, сопоставление пресс-релизов судов, LLM-судья), упомянутый в старых заметках сессий, в текущем дереве отсутствует и не восстановлен — здесь не описан. Chunking и dense/hybrid/reranked-hybrid поиск (Qdrant) в проекте были, но удалены — см. [ADR 0002](../adr/0002-drop-dense-hybrid-search.md).

## Страницы

- [Implementation Status](Implementation-Status.md) — единственный источник текущего статуса: проверки последнего прогона, компоненты, CLI, профили, ограничения
- [Getting Started](Getting-Started.md) — быстрый локальный запуск, ручной pipeline, API, проверки, monitoring, production-like profile
- [Overview](Overview.md) — архитектура целиком, поток данных
- [Ingestion](Ingestion.md) — загрузка, разбор HTML, полный текст статьи
- [Data-Model](Data-Model.md) — таблицы PostgreSQL, persistence
- [Extraction](Extraction.md) — mention extraction, normalization, events, metrics
- [Search](Search.md) — lexical (Postgres) поиск
- [Research-Workflow](Research-Workflow.md) — natural-language запросы: LangGraph + Together AI → `ResearchRequest` → `ResearchService`
- [Research](Research.md) — детерминированный research layer: `ResearchRequest` → `ResearchService` → Person-результаты с evidence
- [Research-Reports](Research-Reports.md) — отчёт с claims/citations, human review policy, database-first source routing
- [Semantic-Retrieval](Semantic-Retrieval.md) — entity-level lexical + dense + RRF кандидаты (Qdrant), evaluation backend'ов
- [Entity-Resolution](Entity-Resolution.md) — ER v2: matching_key как ключ кандидатов (тёзки), pg_trgm/semantic кандидаты, признаки, решение AUTO_LINK/REVIEW/CREATE_NEW, human review
- [Architecture](Architecture.md) — компоненты и границы: домен, orchestration (Dagster), хранилища
- [Monitoring](Monitoring.md) — автоматический monitoring pipeline: Dagster, runs, checkpoints, findings, CLI/API
- [Local Web UI](Local-Web-UI.md) — локальная веб-морда для ER-ревью, карточки Person, evidence spans и lexical search
- [Evaluation](Evaluation.md) — оценка качества поиска, baseline-отчёты
- [Real-World Validation](RealWorldValidation.md) — real-world corpus, golden annotations, safety gates, отчёты качества pipeline
- [Setup](Setup.md) — переменные окружения, docker compose, миграции, CLI
- [Testing](Testing.md) — тесты, линтеры, pre-commit

Глоссарий доменных терминов — [`CONTEXT.md`](../../CONTEXT.md) в корне репозитория. Архитектурные решения — [`docs/adr/`](../adr/).

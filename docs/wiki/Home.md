# court-monitor — вики

`court-monitor` — восстановленный первый вертикальный срез проекта (случайная потеря файлов, см. `README.md`). Пайплайн загрузки статей ОВД-Инфо → разбора → сохранения полного текста в PostgreSQL → lexical-поиска → оценки качества поиска.

Эта вики покрывает **только код, реально присутствующий в репозитории на момент написания**. Более ранний функционал (сопоставление ФИО с реестром, сопоставление пресс-релизов судов, LLM-судья), упомянутый в старых заметках сессий, в текущем дереве отсутствует и не восстановлен — здесь не описан. Chunking и dense/hybrid/reranked-hybrid поиск (Qdrant) в проекте были, но удалены — см. [ADR 0002](../adr/0002-drop-dense-hybrid-search.md).

## Страницы

- [Overview](Overview.md) — архитектура целиком, поток данных
- [Ingestion](Ingestion.md) — загрузка, разбор HTML, полный текст статьи
- [Data-Model](Data-Model.md) — таблицы PostgreSQL, persistence
- [Search](Search.md) — lexical (Postgres) поиск
- [Evaluation](Evaluation.md) — оценка качества поиска, baseline-отчёты
- [Setup](Setup.md) — переменные окружения, docker compose, миграции, CLI
- [Testing](Testing.md) — тесты, линтеры, pre-commit

Глоссарий доменных терминов — [`CONTEXT.md`](../../CONTEXT.md) в корне репозитория. Архитектурные решения — [`docs/adr/`](../adr/).

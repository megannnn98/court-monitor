# court-monitor — вики

`court-monitor` — восстановленный первый вертикальный срез проекта (случайная потеря файлов, см. `README.md`). Пайплайн загрузки статей ОВД-Инфо → разбора → chunking → сохранения в PostgreSQL → поиска (lexical и dense) → оценки качества поиска.

Эта вики покрывает **только код, реально присутствующий в репозитории на момент написания**. Более ранний функционал (сопоставление ФИО с реестром, сопоставление пресс-релизов судов, LLM-судья), упомянутый в старых заметках сессий, в текущем дереве отсутствует и не восстановлен — здесь не описан.

## Страницы

- [Overview](Overview.md) — архитектура целиком, поток данных
- [Ingestion](Ingestion.md) — загрузка, разбор HTML, chunking
- [Data-Model](Data-Model.md) — таблицы PostgreSQL, persistence
- [Search](Search.md) — lexical (Postgres) и dense (Qdrant) поиск
- [Evaluation](Evaluation.md) — оценка качества поиска, baseline-отчёты
- [Setup](Setup.md) — переменные окружения, docker compose, миграции, CLI
- [Testing](Testing.md) — тесты, линтеры, pre-commit

Глоссарий доменных терминов — [`CONTEXT.md`](../../CONTEXT.md) в корне репозитория. Архитектурные решения — [`docs/adr/`](../adr/).

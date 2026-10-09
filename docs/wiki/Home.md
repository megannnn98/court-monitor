# court-monitor — вики

`court-monitor` собирает публикации из источников, извлекает людей и события,
сводит упоминания в людей, определяет, на кого заведено уголовное дело и
политическое ли оно, сверяет людей с перечнем Росфинмониторинга (он подтверждает
личность, а не отсеивает) и опознаёт безымянных фигурантов. Результат — список
политических уголовных дел в консоли «Следователь».

Вики рассчитана на две роли:

- оператору она дает рабочие сценарии: что запустить, где посмотреть результат,
  как понять ошибку;
- программисту она дает карту кода: модули, таблицы, тесты, ограничения.

Правила оформления страниц: [Wiki Style Guide](Wiki-Style-Guide.md). Текущий
статус проекта и последние проверенные команды: [Implementation
Status](Implementation-Status.md).

## Быстрый путь

1. Запустить проект локально: [Getting Started](Getting-Started.md).
2. Понять общий поток данных: [Overview](Overview.md).
3. Разобрать автоматическую докачку и статусы: [Monitoring](Monitoring.md).
4. Работать в консоли «Следователь»: [Local Web UI](Local-Web-UI.md); домашняя
   страница ведёт по пяти шагам и проверкам рабочего цикла — [Pipeline](Pipeline.md).
5. Проверять качество: [Real-World Validation](RealWorldValidation.md).

## Карта страниц

- [Implementation Status](Implementation-Status.md) — единственный источник текущего статуса: проверки последнего прогона, компоненты, CLI, профили, ограничения
- [Getting Started](Getting-Started.md) — быстрый локальный запуск, ручной pipeline, API, проверки, monitoring, production-like profile
- [Overview](Overview.md) — архитектура целиком, поток данных
- [Ingestion](Ingestion.md) — загрузка, разбор HTML, полный текст статьи
- [Monitor Core](Monitor-Core.md) — переиспользуемое ядро: модели и протоколы ingestion, конвейер, OpenAI-совместимый транспорт, retry; границы ядра и что осталось в приложении
- [Data-Model](Data-Model.md) — таблицы PostgreSQL, persistence
- [Extraction](Extraction.md) — mention extraction, normalization, events, metrics
- [Entity-Resolution](Entity-Resolution.md) — ER v2: matching_key как ключ кандидатов (тёзки), pg_trgm кандидаты, признаки, решение AUTO_LINK/REVIEW/CREATE_NEW, human review
- [Architecture](Architecture.md) — компоненты и границы: домен, orchestration (Dagster), хранилища
- [Monitoring](Monitoring.md) — автоматический monitoring pipeline: Dagster, runs, checkpoints, findings, CLI/API
- [Local Web UI](Local-Web-UI.md) — консоль «Следователь»: меню из трёх групп, «Работа», «Результат», досье, отдельные проверки и журнал запусков
- [Pipeline](Pipeline.md) — пять шагов от публикации до «Результата», модель и расходы, старый путь через Person
- [Investigation Graph](Investigation-Graph.md) — граф событий в досье: человек, события, а через них публикации, суды, статьи и другие люди
- [Unnamed Figurants](Unnamed-Figurants.md) — безымянные фигуранты и кандидаты на них из перечня Росфинмониторинга
- [Ask](Ask.md) — «Спросить»: вопросы к базе обычными словами, ответы по подсчётам над приговорами
- [Junk Screen](Junk-Screen.md) — отсев мусора с проверкой: статьи, где извлечение пропустило дело, удерживаются, а не удаляются
- [Rosfinmonitoring](Rosfinmonitoring.md) — перечень: снимки, сверка людей, подтверждение личности, даты включения, таблица оператора и исключённые из перечня
- [Airtable Sync](Airtable-Sync.md) — ручная синхронизация справочников из Airtable: кнопка, `POST /api/admin/airtable/sync`, соответствие полей, идемпотентность
- [Real-World Validation](RealWorldValidation.md) — real-world corpus, golden annotations, safety gates, отчёты качества pipeline
- [Setup](Setup.md) — переменные окружения, docker compose, миграции, CLI
- [Rebuild-Image](Rebuild-Image.md) — пересборка образа и выпуск: команды, миграции, проверка, уборка места, откат
- [Testing](Testing.md) — тесты, линтеры, pre-commit

## Где искать ответы

- “Как запустить?” — [Getting Started](Getting-Started.md), потом
  [Setup](Setup.md).
- “Почему run упал?” — [Monitoring](Monitoring.md), [Testing](Testing.md).
- “Почему человек попал в «Результат»?” — его досье в консоли, [Pipeline](Pipeline.md),
  [Persecution Classification](Persecution-Classification.md),
  [Rosfinmonitoring](Rosfinmonitoring.md).
- “Почему это один человек или разные?” — [Entity Resolution](Entity-Resolution.md).
- “Где код?” — [Overview](Overview.md) и тематическая страница нужного слоя.

Глоссарий доменных терминов — [`CONTEXT.md`](../../CONTEXT.md). Архитектурные
решения — [`docs/adr/`](../adr/).

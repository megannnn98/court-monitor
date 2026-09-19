# Telegram Bot

Бот — внешний адаптер над существующими сервисами court-monitor ([ADR 0019](../adr/0019-telegram-bot-adapter.md)):

```text
Telegram command → handler → application service → PostgreSQL / monitoring pipeline → ответ
```

Код: `src/telegram_bot/`. Тесты: `tests/telegram_bot/`. Отдельный процесс, long polling,
без webhook и без открытого порта.

## Команды

Все команды закрыты allowlist'ом, включая `/people`.

| Команда | Что делает |
|---|---|
| `/start` | назначение бота и список команд |
| `/help` | синтаксис команд и примеры |
| `/update` | запускает инкрементальную докачку и обработку; отвечает сразу |
| `/status` | состояние последней докачки: этапы, счётчики, ошибки |
| `/people YYYY-MM-DD YYYY-MM-DD [limit]` | люди из новостей за период |

### `/update`

Запускает операцию `monitor` операторской консоли ([ADR 0016](../adr/0016-operator-operation-runs.md)) —
ту же, что кнопка «Докачать новые публикации» в веб-морде. Она поднимает подпроцесс
`main.py monitor --catch-up --limit N`, то есть полный pipeline:

```text
discover → ingest → extract → entity resolution → persecution classification
→ Rosfinmonitoring matching (если есть snapshot) → semantic indexing (если настроен) → findings
```

Handler не ждёт окончания: он отвечает, как только run записан.

```text
Обновление запущено.

Run: #42
Источники: ovd-info, sota-vision, kommersant, …
Режим: incremental

Проверить состояние: /status
```

Второй параллельный запуск невозможен: частичный уникальный индекс
`uq_operator_operation_runs_active_operation` не даёт создать второй живой run, и бот
отвечает состоянием текущего:

```text
Обновление уже выполняется.

Run: #41
Статус: running
Запущено: 14:32
```

### `/status`

Показывает последний run операции `monitor` и агрегирует `monitoring_runs`, которые
оставил его процесс ([ADR 0013](../adr/0013-automated-monitoring.md)). Все счётчики —
реальные поля `monitoring_runs`, ничего вычисляемого «на глаз»:

```text
Run #42
Статус: succeeded
Начало: 2026-09-19 14:32
Окончание: 2026-09-19 14:38

Источников обработано: 5 из 5
Общая обработка: готово
Обнаружено публикаций: 34
Загружено публикаций: 12
Обработано статей: 12
Извлечено событий: 27
Создано людей: 8
Связано упоминаний: 5
Отправлено на review: 3
Ошибок: 1
• ingestion: 1 × ReadTimeout (повторяемая)
```

Ошибки берутся из `monitoring_run_items` и группируются по этапу, виду
(`retryable` / `non_retryable`) и типу исключения. В Telegram уходит только тип
ошибки — ни traceback, ни connection string, ни токен.

**Ограничение.** Связь операции с её monitoring-runs — по интервалу
`[started_at, finished_at]` и триггеру `manual`; отдельного `operation_run_id` в
`monitoring_runs` нет. Если в это же время кто-то запустил докачку вручную из CLI
(`main.py monitor`) или из Dagster-ассета с триггером по умолчанию, их runs попадут
в счётчики `/status`. То же верно для операторской веб-морды — она считает так же
(`src/web/ui/operations.py`). Runs по расписанию Dagster (`trigger = schedule`) в
выдачу не попадают.

### `/people`

```text
/people 2026-09-01 2026-09-19
/people 2026-09-01 2026-09-19 100
```

Ответ:

```text
Люди из новостей 2026-09-01 — 2026-09-19
Найдено всего: 387
Показано: 50

1. Иванов Иван Иванович
   Публикаций: 3
   Последняя: 2026-09-18
   Источники: ОВД-Инфо, SOTA
   Статьи:
   • Заголовок — ссылка
   • Заголовок — ссылка
```

Ошибочный ввод (нет дат, одна дата, `01.09.2026`, `2026-99-99`, начало позже конца,
нечисловой limit) получает формат и пример, без traceback:

```text
Формат:
/people YYYY-MM-DD YYYY-MM-DD [limit]

Пример:
/people 2026-09-01 2026-09-19
```

## Период

Обе даты включаются. Они читаются в таймзоне `TELEGRAM_BOT_TIMEZONE`
(по умолчанию `Europe/Moscow`) и превращаются в полуинтервал, переведённый в UTC —
`published_at` хранится в UTC:

```text
[start_date 00:00:00, end_date + 1 day 00:00:00)
```

Например `/people 2026-09-01 2026-09-19` в московской зоне — это
`[2026-08-31 21:00 UTC, 2026-09-19 21:00 UTC)`.

Основная дата — `ParsedArticle.published_at`. Не используются `Person.created_at`,
дата extraction run, дата загрузки документа и дата создания alias. Статьи без
`published_at` в выдачу не попадают.

## Кто попадает в `/people`

Человек входит в результат, если:

1. есть активная каноническая `Person` (`persons.status = 'active'`);
2. к ней привязано подтверждённое упоминание (`entity_mentions.person_id` заполняется
   только после entity resolution) **или** событие через `person_event_links`;
3. упоминание или событие принадлежит extraction run статьи;
4. `published_at` статьи попадает в период;
5. статья пришла из новостного источника.

Один человек выводится один раз, одна статья учитывается для него один раз — даже
если в ней несколько упоминаний, несколько событий или несколько алиасов.

## Новостные источники

У `SourceDefinition` есть поле `kind: SourceKind` (`src/sources/source_registry.py`):

| kind | источники |
|---|---|
| `news` | ОВД-Инфо, SOTA, «Коммерсантъ», пресс-службы судов (sudrf), Telegram-каналы СМИ |
| `registry` | реестр фигурантов «Мемориала» (`memopzk-figurants`) |

`/people` фильтрует по базовым URL источников с `kind = news`
(`news_source_base_urls()`), а не по имени источника: переименование источника
фильтр не ломает. Канал, для которого court-monitor готовит публикации
([ADR 0017](../adr/0017-channel-feed-sources.md)), входным источником не является и в
реестре `SOURCES` не значится.

## Запрос `/people`

Один SQL, без N+1 (`src/telegram_bot/people_repository.py`):

1. `person_article` — UNION двух путей: `persons → entity_mentions → article_extraction_runs
   → parsed_articles → source_documents → sources` и `persons → person_event_links →
   extracted_events → article_extraction_runs → …`. Именно UNION (не UNION ALL)
   дедуплицирует пары `(person_id, article_id)`;
2. `person_stats` — `COUNT(*)`, `MAX(published_at)` и `array_agg(DISTINCT source_name)`
   по человеку;
3. `ranked_people` — `COUNT(*) OVER ()` (это `Найдено всего`, считается **до** limit) и
   `ROW_NUMBER() OVER (ORDER BY latest_published_at DESC, canonical_name, person_id)`;
4. `page` — строки с `position <= limit`;
5. `LATERAL` — не более трёх последних статей каждого человека
   (`ORDER BY published_at DESC, article_id DESC LIMIT 3`).

Индексы под запрос (миграция `v6w7x8y9z0a1`):

| индекс | зачем |
|---|---|
| `ix_parsed_articles_published_at` | фильтр периода |
| `ix_extracted_events_extraction_run_id` | путь событие → extraction run (FK индекса не создаёт) |
| `ix_entity_mentions_person_id` | уже существовал: путь человек → упоминания |
| `uq_source_documents_source_id_external_id`, `sources.base_url` unique | префикс для join'ов и фильтра источника |

Ответ разбивается на несколько сообщений (`message_splitter.py`): каждая часть меньше
лимита Telegram, ссылка и HTML-сущность никогда не разрываются, порядок частей
сохраняется, число частей ограничено (иначе ответ обрезается с пояснением). Чтобы
ссылка не могла оказаться разорванной между двумя сообщениями, `formatting.py`
укорачивает имя (150 символов) и заголовок статьи (300): строка блока всегда короче
одного сообщения.

## Авторизация

```text
TELEGRAM_ALLOWED_USER_IDS=123456789,987654321
```

Только числовые `user_id` — username может смениться, id нет. Пустой или
некорректный allowlist не запускает бота (fail-fast). Неавторизованный пользователь
получает «Доступ закрыт» и не запускает pipeline. Решение об авторизации пишется в
лог (`event=telegram_authorization command=… telegram_user_id=… result=allowed|denied`),
текст сообщений — нет.

## Конфигурация

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — (обязательна) | токен бота; в репозитории не хранится |
| `TELEGRAM_ALLOWED_USER_IDS` | — (обязательна) | числовые user id через запятую |
| `TELEGRAM_BOT_TIMEZONE` | `Europe/Moscow` | в какой зоне читаются даты `/people` |
| `TELEGRAM_PEOPLE_DEFAULT_LIMIT` | `50` | limit по умолчанию |
| `TELEGRAM_PEOPLE_MAX_LIMIT` | `200` | максимально допустимый limit |
| `DATABASE_URL` | — (обязательна) | база court-monitor |
| `MONITORING_ENABLED_SOURCES`, `MONITORING_DISCOVERY_LIMIT` | как у остального приложения | что и сколько качает `/update` |

Fail-fast: отсутствующий токен, неизвестная таймзона, некорректный allowlist, limit вне
диапазона и отсутствующий `DATABASE_URL` не дают боту стартовать; все проблемы
сообщаются одним списком. В логе конфигурации токен заменён на `***`.

## Запуск локально

```bash
export DATABASE_URL=postgresql+psycopg://court_monitor:...@localhost:5433/court_monitor
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_ALLOWED_USER_IDS=123456789
uv run python -m telegram_bot
```

## Запуск через Docker

```bash
docker compose up -d postgres
docker compose run --rm migrate
TELEGRAM_BOT_TOKEN=... TELEGRAM_ALLOWED_USER_IDS=123456789 \
  docker compose --profile telegram up -d telegram-bot
```

Сервис использует существующий образ `court-monitor:local`, ждёт healthy PostgreSQL,
не публикует портов, получает токен только из окружения, перезапускается
(`restart: unless-stopped`), работает под `init: true` и корректно завершается по
SIGTERM. Миграции автоматически не выполняются — только отдельным `migrate`.

## Troubleshooting

| Симптом | Причина и что делать |
|---|---|
| контейнер падает сразу со списком проблем | fail-fast конфигурации: читайте список в логе, он называет каждую переменную |
| `required variable TELEGRAM_BOT_TOKEN is missing a value` при `compose up` | токен не экспортирован в окружении: он намеренно не хранится в compose и в репозитории |
| бот молчит на все команды | ваш `user_id` не в allowlist; в логе `event=telegram_authorization … result=denied` |
| `/update` отвечает «уже выполняется» | живой run операции `monitor`; дождитесь его или посмотрите `/status` |
| `/status` показывает `interrupted` | процесс операции умер без heartbeat (см. ADR 0016); запустите `/update` снова |
| `/people` ничего не находит | в периоде нет статей новостных источников с заполненным `published_at`, либо докачка не выполнялась — проверьте `/status` |
| ответ обрывается фразой «ответ слишком длинный» | сузьте период или уменьшите limit |

## Ограничения первой версии

- **Нет healthcheck у контейнера.** При зависании polling-цикла контейнер останется
  `Up`: `restart: unless-stopped` перезапускает только упавший процесс. Первая версия
  полагается на логи; healthcheck потребовал бы собственной метки живости.
- **Новостные источники берутся из реестра кода, не из таблицы `sources`.** Если
  `base_url` источника изменится в `SOURCES`, статьи, сохранённые под прежним URL,
  перестанут попадать в `/people`, а удалённый из реестра источник выпадет целиком.
  Следующий шаг — хранить `kind` в таблице `sources` и заполнять его при ingestion.
- **`/status` смешивает параллельные ручные докачки** (см. раздел `/status`).

- Только чтение домена и запуск докачки: бот не правит Person, не ведёт ER/persecution/РФМ review, не добавляет источники, не публикует в канал.
- Нет уведомления инициатора по завершении run: aiogram умеет отправить сообщение, но операцию исполняет отдельный процесс, который о Telegram ничего не знает; состояние смотрят через `/status`.
- Нет webhook, inline-режима, FSM, свободного текста и LLM.
- `/people` не показывает события и классификацию — только людей, статьи и источники.
- `/status` описывает последнюю операцию `monitor`, а не историю запусков.

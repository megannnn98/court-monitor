# Справочник CLI

Полный перечень команд. Пошаговый сценарий работы — в [README](../README.md);
здесь только «что за команда и какие у неё флаги».

Все команды запускаются через `uv run court-monitor <команда>` (или
`.venv/bin/court-monitor <команда>`). `court-monitor --help` покажет тот же
список, `court-monitor <команда> --help` — актуальные флаги.

---

## База данных и окружение

| Команда | Что делает |
|---|---|
| `init-db` | Создать схему (внутри — `alembic upgrade head`) |
| `migrate` | Применить миграции (на этом этапе — алиас `init-db`) |
| `doctor` | Проверка окружения: конфиг, БД, миграции, репозиторий |
| `show-config` | Показать текущую конфигурацию БД |

`doctor` — первое, что стоит запускать при любой непонятной ошибке. Он
отдельно проверяет, что БД не отстала от миграций: это самый частый способ
получить `no such column` посреди долгого сбора.

## Реестр источников

| Команда | Флаги |
|---|---|
| `import-source-registry` | `--from-airtable`, `--from-csv <path>`, `--view-url <url>`, `--dry-run` / `--no-dry-run` (по умолчанию `--dry-run`), `--registry-path <path>` |
| `check-sources` | `--registry-path <path>` |
| `list-sources` | — |

`import-source-registry` по умолчанию работает в режиме предпросмотра —
чтобы записать `config/source_registry.yaml`, нужен `--no-dry-run`.

`check-sources` делает по одному вежливому HTTP-запросу на источник и
показывает, кто отвечает, а кто блокирует.

## Сбор данных

| Команда | Флаги |
|---|---|
| `fetch-source <name>` | `--live`, `--limit <n>`, `--no-parse`, `--file <path>`, `--dry-run` |
| `fetch-all` | — |
| `run-all` | `--live`, `--verbose` / `-v` |
| `fetch-demo-source` | — |

Без `--live` всё работает на фикстурах и в сеть не ходит — это нормальный
режим для проверки, что конвейер цел.

`run-all` = все sudrf-источники + Telegram-каналы из реестра + `generate-matches`
одной командой. По умолчанию подробные per-document логи подавлены, чтобы
итоговые построчные сводки по источникам можно было прочитать; `--verbose`
возвращает полный структурированный JSON-лог.

`fetch-source fedsfm` — импорт перечня Росфинмониторинга:

```bash
fetch-source fedsfm --live              # живая загрузка с fedsfm.ru
fetch-source fedsfm --file <path>       # из локального XML / DBF / ZIP / CSV
fetch-source fedsfm --file <path> --dry-run   # предпросмотр без записи
```

## Документы и факты

| Команда | Флаги |
|---|---|
| `parse-pending` | — |
| `reprocess-document <id>` | `--force` |
| `reprocess-all` | `--force`, `--limit <n>`, `--dry-run`, `--verbose` / `-v` |
| `list-documents` | `--limit <n>` (по умолчанию 50) |
| `show-document <id>` | — |
| `show-stats` | — |

`reprocess-document` откажется работать, если перепарсинг уничтожит уже
принятые оператором решения по совпадениям — `--force` это подавляет.

`reprocess-all` применяет текущие правила извлечения ко **всему** уже собранному
корпусу. Нужен потому, что исправление экстрактора живёт в коде и само по себе
не доходит до данных: факты остаются такими, какими их вытащили при загрузке.
Та же защита, но проверяется один раз на весь корпус, а не по документу —
иначе запуск, который снесёт решения оператора, успел бы удалить половину
фактов до первого защищённого документа. `--dry-run` показывает дельту и
откатывается.

## Реестр лиц (Росфинмониторинг)

| Команда | Флаги |
|---|---|
| `list-person-records` | `--source <str>` (по умолчанию `rfm`), `--limit <n>` |
| `show-person-record <id>` | — |

## Совпадения

| Команда | Флаги |
|---|---|
| `generate-matches` | — |
| `list-matches` | `--status pending\|confirmed\|rejected`, `--limit <n>` |
| `show-match <id>` | — |
| `confirm-match <id>` | `--comment <str>`, `--operator <str>` |
| `reject-match <id>` | `--comment <str>`, `--operator <str>` |

Автоматического подтверждения нет и не планируется: `generate-matches` только
создаёт кандидатов, решение всегда за человеком. Каждый confirm/reject пишется
в `AuditLog` вместе с actor, старым и новым статусом и correlation_id.

Как считается score и почему на практике почти все кандидаты равны 0.50 —
см. [`ai-context/matching.md`](ai-context/matching.md).

## Очередь проверки и аудит

| Команда | Флаги |
|---|---|
| `list-review-items` | `--status pending\|resolved\|dismissed`, `--type <str>`, `--limit <n>` |
| `resolve-review-item <id>` | `--comment <str>`, `--dismiss`, `--operator <str>` |
| `list-audit-log` | `--type match_candidate\|review_item`, `--id <n>`, `--limit <n>` |

Сбои парсера (`parser_status=parser_failed`) попадают в очередь проверки
автоматически, а не только в лог — иначе сломанный селектор молча теряет
материалы. Ручная проверка этого механизма описана в
[`review-and-audit-testing.md`](review-and-audit-testing.md).

## Веб-интерфейс

| Команда | Флаги |
|---|---|
| `run-web` | `--host <addr>`, `--port <n>`, `--reload` |

Страницы и настройки — в [`configuration.md`](configuration.md).

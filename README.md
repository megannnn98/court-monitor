# court-monitor

Полуавтоматическая система мониторинга уголовных дел по открытым источникам.

## Что это делает?

Собирает информацию о уголовных делах из публичных источников, извлекает из неё факты (статьи УК, даты, ФИО) и помогает оператору находить связи между делами и реестрами (например, Росфинмониторинг). Ничего не публикует и не принимает решения автоматически — всегда нужен человек.

**Источники:**
- Пресс-релизы судов (`sudrf.ru`)
- Перечень Росфинмониторинга (`fedsfm.ru`, XML/DBF/CSV)
- Telegram-каналы
- Airtable (реестр источников)

## Быстрый старт

```bash
# 1. Установить зависимости
make install

# 2. Создать базу данных (SQLite по умолчанию)
make init-db

# 3. Проверить, что всё работает
make doctor
```

## Пример использования

### Загрузить и распарсить пресс-релиз суда

```bash
# Скачать пресс-релиз (заглушка для демо)
uv run court-monitor fetch-source 2zovs

# Распарсить скачанные документы
uv run court-monitor parse-pending

# Посмотреть статистику
uv run court-monitor show-stats

# Посмотреть содержимое конкретного документа
uv run court-monitor show-document 1
```

### Импорт данных Росфинмониторинга

```bash
# Импортировать файл перечня
uv run court-monitor fetch-source fedsfm --file tests/fixtures/rfm/persons.xml

# Посмотреть список записей
uv run court-monitor list-person-records
```

### Поиск связей между делами и реестрами

```bash
# Сгенерировать кандидатов на совпадение
uv run court-monitor generate-matches

# Посмотреть список совпадений
uv run court-monitor list-matches

# Детали конкретного совпадения (с объяснением score)
uv run court-monitor show-match 1

# Подтвердить или отклонить (только вручную!)
uv run court-monitor confirm-match 1 --comment "Подтверждено оператором" --operator "имя"
uv run court-monitor reject-match 1 --comment "Не совпадает" --operator "имя"
```

Каждое confirm/reject записывается в `AuditLog` (actor, старый/новый статус,
correlation_id) — см. `docs/review-and-audit-testing.md`.

### Очередь проверки оператора (ReviewItem)

Сбои парсера (`parser_status=parser_failed`) автоматически попадают в очередь
проверки, а не просто в лог:

```bash
uv run court-monitor list-review-items [--status pending] [--type parser_failed]
uv run court-monitor resolve-review-item <id> --comment "починил селектор" [--dismiss]
```

## CLI — все команды

```bash
# Справка
court-monitor --help

# БД и миграции
court-monitor init-db            # создать схему
court-monitor migrate            # применить миграции
court-monitor doctor             # проверка окружения
court-monitor show-config        # текущая конфигурация

# Источники
court-monitor list-sources                               # список источников
court-monitor fetch-source <name>                        # загрузить и распарсить
court-monitor fetch-source <name> --no-parse             # только загрузить
court-monitor fetch-source fedsfm --file <path>          # импорт из файла
court-monitor fetch-source fedsfm --file <path> --dry-run  # предпросмотр
court-monitor fetch-all                                  # только sudrf-источники (config/sources.yaml)
court-monitor run-all [--live] [--verbose]               # sudrf + все Telegram-каналы + generate-matches, разом
                                                          # цветной построчный вывод; --verbose — полный JSON-лог

# Документы
court-monitor parse-pending                              # распарсить pending
court-monitor reprocess-document <id>                    # перепарсить
court-monitor list-documents                             # список
court-monitor show-document <id>                         # детали + факты

# Росфинмониторинг
court-monitor list-person-records                        # список записей
court-monitor show-person-record <id>                    # детали записи

# Совпадения
court-monitor generate-matches                           # генерация кандидатов
court-monitor list-matches [--status pending]            # список
court-monitor show-match <id>                            # детали
court-monitor confirm-match <id> --comment "..." [--operator ...]  # подтвердить
court-monitor reject-match <id> --comment "..." [--operator ...]   # отклонить

# Очередь проверки оператора (ReviewItem) и аудит
court-monitor list-review-items [--status pending] [--type parser_failed]  # список
court-monitor resolve-review-item <id> --comment "..." [--dismiss]         # разрешить/отклонить
```

## Что извлекается из документов

| Что | Пример |
|---|---|
| Статья УК | `п. «а» ч. 2 ст. 205 УК РФ` |
| Дата | `2026-04-02 (дата приговора)` |
| ФИО | `Иванов Иван Иванович (уверенность 0.95)` |

## Как работает сопоставление людей

Система сравнивает ФИО из судебных документов с записями из реестра Росфинмониторинга. По умолчанию **ничего не подтверждает автоматически** — создаёт кандидатов с объяснением оценки, а оператор решает.

Пример оценки:

| Совпадение | Вес |
|---|---|
| Полное ФИО | +0.70 |
| Фамилия + имя | +0.50 |
| Фамилия + инициалы | +0.35 |
| Дата рождения (полная) | +0.20 |
| Год рождения | +0.15 |
| Конфликт года рождения | -0.60 |

Порог: `score ≥ 0.45` — создаётся кандидат для проверки.

## Веб-интерфейс оператора

Веб-интерфейс позволяет просматривать документы, проверять совпадения
и запускать фоновые задачи сбора данных — без командной строки.

```bash
# Запустить веб-интерфейс (по умолчанию http://127.0.0.1:8010)
make run-web

# Смена адреса/порта
uv run court-monitor run-web --host 0.0.0.0 --port 9090

# Автоперезагрузка при изменении кода
uv run court-monitor run-web --reload
```

Интерфейс привязывается к loopback по умолчанию — аутентификации пока нет
(D-007), поэтому при смене хоста на внешний вы получите предупреждение.

### Доступные страницы

| Страница | URL | Описание |
|---|---|---|
| Дашборд | `/` | Общая статистика, последние документы |
| Документы | `/documents` | Список загруженных материалов |
| Детали документа | `/documents/<id>` | Извлечённые факты |
| Совпадения | `/matches` | Кандидаты на сопоставление с реестром |
| Детали совпадения | `/matches/<id>` | Score breakdown, подтвердить/отклонить |
| Очередь проверки | `/review` | Сбои парсера, блокировки источников |
| Реестр | `/registry` | Поиск по записям Росфинмониторинга |
| Аудит | `/audit` | Журнал решений оператора |
| Задачи | `/jobs` | Запуск и мониторинг фоновых задач |

### JSON API (read-only)

```bash
# Запустить API (порт 8000)
make run-api

# Проверить
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/stats
curl http://127.0.0.1:8000/documents
```

## Docker

```bash
cp .env.example .env
make docker-up    # PostgreSQL + FastAPI на :8000
```

## Переменные окружения

Копируются в `.env` (никогда не коммитятся). Префикс `CM_`. Минимум:

| Переменная | По умолчанию | Описание |
|---|---|---|
| `CM_DATABASE_URL` | `sqlite:///./court_monitor.db` | Строка подключения к БД |
| `CM_LOG_LEVEL` | `INFO` | Уровень логирования |
| `CM_AIRTABLE_MODE` | `read_only` | Режим Airtable |
| `CM_LLM_MODE` | `disabled` | LLM-извлечение (выключено) |

## Тесты

```bash
make test            # все тесты (216)
make test-unit       # unit-тесты
make test-integration  # integration-тесты
```

## Документация

- `docs/ai-context/architecture.md` — архитектура
- `docs/ai-context/extraction.md` — извлечение фактов
- `docs/ai-context/matching.md` — сопоставление людей
- `docs/ai-context/known-risks-and-notes.md` — известные риски
- `docs/review-and-audit-testing.md` — как вручную проверить ReviewItem/AuditLog (очередь проверки оператора + аудит-лог)

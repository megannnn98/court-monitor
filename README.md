# court-monitor

Полуавтоматическая OSINT-система мониторинга уголовных дел по открытым источникам:
пресс-релизы судов на платформе `sudrf.ru`, перечень Росфинмониторинга, синхронизация
с Airtable. Все неоднозначные решения принимает оператор через очередь ручной проверки.

> Статус: MVP-каркас + вертикальный срез (Etap 0–1). Источники работают на
> сохранённых fixtures; live-доступ и запись в Airtable отключены до подтверждения.

## Принципы

- **Факт vs предположение.** Каждое значение поля — `ExtractedFact` со статусом
  (`confirmed` / `inferred` / `unverified` / `conflicting` / `rejected`), цитатой,
  источником и confidence.
- **Источник каждого факта** сохраняется (URL, дата, фрагмент, хеш).
- **Ничего опасного автоматически.** Создание/объединение/публикация — только
  через ручное подтверждение в очереди.
- **Fixtures-first.** Парсеры тестируются на сохранённых HTML, без live-сети.

Подробнее: `docs/discovery.md`, `docs/airtable-discovery.md`.

## Быстрый старт

```bash
# 1. Зависимости (uv сам поставит Python при необходимости)
make install

# 2. Схема БД (SQLite по умолчанию, см. .env.example для PostgreSQL)
make init-db            # = court-monitor init-db (alembic upgrade head)

# 3. Проверка окружения
make doctor

# 4. Вертикальный срез: загрузить fixture пресс-релиз и распарсить
uv run court-monitor fetch-source 2zovs
uv run court-monitor parse-pending
uv run court-monitor show-stats
uv run court-monitor show-document 1

# 5. Тесты и проверки
make lint typecheck test
```

## Docker Compose

```bash
cp .env.example .env              # при необходимости отредактировать
make docker-up                    # postgres + app (FastAPI на :8000)
# или
docker compose up --build
```

После старта: `GET http://localhost:8000/health`, `GET /docs`.

## Переменные окружения

См. `.env.example`. Секреты (Airtable token, LLM key) **никогда** не коммитятся.
Префикс `CM_`. Минимум для старта: `CM_DATABASE_URL`.

## CLI

```bash
court-monitor init-db                  # создать схему
court-monitor migrate                  # применить миграции
court-monitor doctor                   # диагностика окружения
court-monitor fetch-source <name>      # забрать документы источника
court-monitor fetch-all                # все включённые источники
court-monitor parse-pending            # разобрать непарсенные документы
court-monitor reprocess-document <id>  # перепарсить один документ
court-monitor show-stats               # сводка по БД
court-monitor show-document <id>       # показать документ и его факты
```

## API

`GET /health`, `GET /documents`, `GET /documents/{id}`, `GET /stats`.
Полный набор (`/review/*`, `/audit`) — на Etap 7.

## Добавление нового суда

1. Добавьте запись в `config/sources.yaml` (`type: sudrf`, `backend`, `paths`,
   `fixture_path` или live URL).
2. При нестандартной вёрстке — переопределите селекторы/парсер (см.
   `docs/source-adapters.md`, готовится).
3. Положите образец HTML в `tests/fixtures/html/` и добавьте regression-тест.

## Добавление статьи / ключевого слова

Отредактируйте `config/monitoring.yaml`. Перезапуск не требует изменения кода.

## Безопасная эксплуатация

- `CM_AIRTABLE_MODE=read_only` до валидации маппинга (`docs/airtable-discovery.md`).
- LLM отключена (`CM_LLM_MODE=disabled`); regex-экстракция детерминирована.
- Логи — структурированный JSON; персональные данные без нужды не логируются.
- CAPTCHA/блокировки не обходятся — создаётся `ReviewItem` оператору.

## Тесты

```bash
make test               # все
make test-unit          # только unit
make test-integration   # только integration
```

## Документация

- `docs/discovery.md` — исследование источников и ограничения.
- `docs/airtable-discovery.md` — исследование Airtable.
- `docs/technical-debt.md` — известный технический долг.
- `CHANGELOG.md` — история изменений.

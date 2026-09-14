# Getting Started с court-monitor

## Что это запускает

Основной сценарий:

```text
источники -> статьи -> extraction -> canonical persons -> persecution
-> Rosfinmonitoring match -> candidates
```

Минимальный полезный прогон без файла Росфинмониторинга уже показывает найденных
людей и их классификацию. Rosfinmonitoring нужен только для финального списка
`list-candidates`.

## 1. Поднять окружение

```bash
cd /home/b/Documents/ebnv

uv sync --frozen
cp -n .env.example .env
set -a
source .env
set +a

docker compose up -d
uv run alembic upgrade head
```

Проверить, что база доступна и схема на последней миграции:

```bash
uv run alembic current
uv run alembic heads
```

Обе команды должны показывать один и тот же head revision.

## 2. Загрузить небольшой набор статей

```bash
set -a
source .env
set +a

uv run python src/main.py discover-and-ingest --source ovd-info --limit 20
```

Альтернативно загрузить одну конкретную статью:

```bash
uv run python src/main.py ingest "https://ovd.info/news/example"
```

## 3. Извлечь сущности и события

```bash
uv run python src/main.py extract-entities
```

Проверить счётчики:

```bash
PSQL_URL="${DATABASE_URL/+psycopg/}"
psql "$PSQL_URL" -c "
select 'parsed_articles' as table_name, count(*) from parsed_articles
union all select 'entity_mentions', count(*) from entity_mentions
union all select 'extracted_events', count(*) from extracted_events
order by table_name;
"
```

## 4. Найти и склеить людей

```bash
uv run python src/main.py resolve-people
```

Посмотреть найденных canonical persons:

```bash
PSQL_URL="${DATABASE_URL/+psycopg/}"
psql "$PSQL_URL" -c "
select
  p.id,
  p.canonical_name,
  p.status,
  count(distinct m.id) as mentions,
  count(distinct a.id) as aliases
from persons p
left join entity_mentions m on m.person_id = p.id
left join person_aliases a on a.person_id = p.id
group by p.id, p.canonical_name, p.status
order by mentions desc, p.id
limit 30;
"
```

Посмотреть pending review по неоднозначным людям:

```bash
uv run python src/main.py person-resolution-reviews list
```

## 5. Классифицировать преследование

```bash
uv run python src/main.py classify-persecution
```

Посмотреть людей с последней классификацией:

```bash
PSQL_URL="${DATABASE_URL/+psycopg/}"
psql "$PSQL_URL" -c "
with latest as (
  select distinct on (person_id)
    person_id,
    status,
    confidence,
    reasons,
    classified_at
  from persecution_classifications
  order by person_id, classified_at desc, id desc
)
select
  p.id,
  p.canonical_name,
  latest.status,
  latest.confidence,
  latest.reasons
from latest
join persons p on p.id = latest.person_id
order by latest.confidence desc, p.id
limit 30;
"
```

## 6. Optional: проверить Rosfinmonitoring snapshots

Текущий CLI умеет матчить по уже существующему `snapshot_id`, но не имеет
отдельной команды импорта/list snapshots. Проверить существующие snapshots можно
через PostgreSQL:

```bash
PSQL_URL="${DATABASE_URL/+psycopg/}"
psql "$PSQL_URL" -c "
select id, snapshot_date, source_url, fetched_at, created_at, entry_count
from rosfinmonitoring_snapshots
order by id desc
limit 20;
"
```

Или через API:

```bash
# терминал 1
uv run uvicorn --app-dir src api:app --reload --port 8001
```

```bash
# терминал 2
curl "http://localhost:8001/rosfinmonitoring/snapshots"
```

Если запрос вернул пустой список, Rosfinmonitoring-часть пока пропустить:
в этой базе нет snapshot, с которым можно матчить людей.

Если snapshots есть, запомнить реальный `snapshot_id` из вывода.

## 7. Optional: получить финальных кандидатов

```bash
PSQL_URL="${DATABASE_URL/+psycopg/}"
SNAPSHOT_ID="$(
  psql "$PSQL_URL" -At -c "
  select id
  from rosfinmonitoring_snapshots
  order by snapshot_date desc, id desc
  limit 1;
  "
)"

if [ -z "$SNAPSHOT_ID" ]; then
  echo "No Rosfinmonitoring snapshot in this database; skip this optional step."
else
  uv run python src/main.py match-rosfinmonitoring --snapshot-id "$SNAPSHOT_ID"
  uv run python src/main.py list-candidates \
    --snapshot-id "$SNAPSHOT_ID" \
    --min-confidence 0.7 \
    --output-path candidates.json
fi
```

`candidates.json` содержит людей, у которых:

- последняя классификация `political`;
- confidence не ниже `--min-confidence`;
- Rosfinmonitoring status для snapshot = confirmed `NOT_MATCHED`.

Если snapshots нет, основной ручной результат смотри на шагах 4-5: найденные
люди, их mentions/aliases и последняя persecution classification.

Не подставляй примерное значение, если такого snapshot нет в базе: команды
`match-rosfinmonitoring` и `list-candidates` честно завершатся с
`Rosfinmonitoring snapshot <id> not found`.

## 8. Natural-language поиск

Нужны `TOGETHER_API_KEY` и `TOGETHER_MODEL` в `.env`. LLM только переводит
вопрос в структурированный `ResearchRequest`; факты берутся из PostgreSQL.

```bash
uv run python src/main.py ask \
  "Найди людей, которых преследовали за антивоенную деятельность" \
  --show-request
```

## 9. API

```bash
# терминал 1
set -a
source .env
set +a

uv run uvicorn --app-dir src api:app --reload --port 8001
```

Открыть:

```text
http://localhost:8001/docs
```

Примеры:

```bash
# терминал 2
curl "http://localhost:8001/health"
curl "http://localhost:8001/candidates?snapshot_id=<real-snapshot-id>&min_confidence=0.7"
```

Если порт `8001` тоже занят, выбрать другой:

```bash
uv run uvicorn --app-dir src api:app --reload --port 8002
```

## 10. Проверки

Быстрая проверка без внешних сервисов:

```bash
uv run pytest
```

Статика:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy --strict src tests
```

Integration tests с PostgreSQL/Qdrant:

```bash
export TEST_DATABASE_URL="postgresql+psycopg://court_monitor:court_monitor_dev@localhost:5433/court_monitor_test"
export QDRANT_TEST_URL="http://127.0.0.1:6333"

DATABASE_URL="$TEST_DATABASE_URL" uv run alembic upgrade head
uv run pytest
```

## Частые проблемы

### `DATABASE_URL environment variable is not set`

Выполнить:

```bash
set -a
source .env
set +a
```

### `alembic current` не равен `alembic heads`

База на старой схеме. Обновить:

```bash
uv run alembic upgrade head
```

### `psql` не понимает `postgresql+psycopg://`

`psql` ждёт обычную схему URL:

```bash
PSQL_URL="${DATABASE_URL/+psycopg/}"
psql "$PSQL_URL"
```

Выйти из `psql`: `\q`. Если prompt стал `court_monitor-#`, команда не
завершена; нажать `Ctrl+C`, потом `\q`.

### В `list-candidates` пусто

Проверить:

- есть ли Rosfinmonitoring snapshot;
- был ли выполнен `match-rosfinmonitoring`;
- есть ли политические классификации с confidence выше порога;
- Rosfin status должен быть именно confirmed `NOT_MATCHED`, не `NO_MATCH_RECORD`.

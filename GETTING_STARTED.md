# Getting Started с court-monitor

## Запуск

### 1. Окружение и БД

```bash
export DATABASE_URL="postgresql://court_monitor:court_monitor_dev@localhost:5433/court_monitor"
uv sync
uv run alembic upgrade head
```

### 2. Полный pipeline

```bash
# 1. Загрузить статьи
uv run python src/main.py discover-and-ingest --source ovd-info --limit 50

# 2. Извлечь сущности (mentions, события)
uv run python src/main.py extract-entities

# 3. Свести упоминания к каноническим персонам
uv run python src/main.py resolve-people

# 4. Классифицировать политическое преследование
uv run python src/main.py classify-persecution

# 5. Импортировать снапшот Росфинмониторинга
uv run python src/main.py import-rosfinmonitoring --file path/to/rosfin.xml

# 6. Сопоставить персон со снапшотом
uv run python src/main.py match-rosfinmonitoring --snapshot-id 1

# 7. Получить итоговых кандидатов
uv run python src/main.py list-candidates --snapshot-id 1 --output-path candidates.json
```

## Как посмотреть результат

**Файл** — шаг 7 пишет результат в `candidates.json` (путь задаётся `--output-path`).

**API:**

```bash
uvicorn src.api:app --reload
# http://localhost:8000/docs
curl "http://localhost:8000/candidates?snapshot_id=1&min_confidence=0.8"
```

**Напрямую из БД:**

```bash
PGPASSWORD=court_monitor_dev psql -h localhost -p 5433 -U court_monitor -d court_monitor \
  -c "SELECT p.canonical_name, pc.status, pc.confidence
      FROM persons p
      JOIN persecution_classifications pc ON p.id = pc.person_id;"
```

## В каком виде результат

`candidates.json` — `CandidateQueryResult`: список кандидатов (политически преследуемые персоны, отсутствующие в Росфинмониторинге).

```json
{
  "snapshot_id": 1,
  "total_count": 2,
  "candidates": [
    {
      "person_id": 42,
      "canonical_name": "Иван Иванов",
      "normalized_name": "Иван Иванов",
      "persecution_status": "political",
      "persecution_confidence": 0.9,
      "persecution_reasons": ["Политическая статья: ст. 282 УК РФ"],
      "rosfinmonitoring_status": "not_in_list",
      "rosfinmonitoring_match_confidence": null,
      "event_count": 3,
      "alias_count": 2,
      "last_event_date": "2026-01-15T00:00:00Z"
    }
  ],
  "query_timestamp": "2026-09-13T19:00:00Z"
}
```

`rosfinmonitoring_status` — одно из: `not_in_list`, `matched`, `ambiguous`, `needs_review`, `no_match_record`. В `candidates.json` попадают только записи, где статус **не** `matched`.

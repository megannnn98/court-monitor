# Контекст — 2026-07-27

## Что сделано
- Etap 0: Discovery — исследование источников (sudrf.ru, Росфинмониторинг, Airtable, Telegram), fixtures-first архитектура, модель данных.
- Etap 1: Каркас пакета + вертикальный срез — конфиг, домен, хранилище, источники, парсеры, экстракция, сервисы, CLI, API, логирование. Миграции Alembic.
- Etap 2: Интеграция парсера sudrf_press в pipeline, реестр источников из Airtable, probe/checkout команды.
- Etap 3: Структурированная экстракция — статьи УК, даты, ФИО. Фильтр релевантности по monitoring.yaml.
- Росфинмониторинг — импорт из XML/DBF/ZIP/CSV, нормализация ФИО, дедупликация по (source, normalized_name, birth_date).
- Explainable matching — морфологическая нормализация (ru-name-v2), scoring с весами,BirthDateEvidence для precision, generation candidates.
- CLI: init-db, migrate, doctor, show-config, fetch-source (sudrf + fedsfm), fetch-all, parse-pending, reprocess-document, list-documents, show-document, show-stats, list-sources, fetch-demo-source, import-source-registry, check-sources, list-person-records, show-person-record, generate-matches, list-matches, show-match, confirm-match, reject-match.
- FastAPI: /health, /stats, /documents, /documents/{id}.
- 134 теста проходят.

## Следующий шаг
- Telegram-источники: парсинг постов из каналов (source_type=telegram в реестре).
- Airtable write-back: запись подтверждённых кандидатов в Airtable (после валидации маппинга).
- Etap 4-5: дополнительные источники, расширенная экстракция.
- Покрытие тестами matching/ и fedsfm.

## Блокировки
- нет

## Ключевые файлы
- `src/court_monitor/cli/app.py` — CLI entrypoint, все команды
- `src/court_monitor/services/__init__.py` — pipeline: fetch → parse → extract → match
- `src/court_monitor/extraction/articles.py` — экстракция статей УК (regex)
- `src/court_monitor/extraction/dates.py` — экстракция дат (русский формат)
- `src/court_monitor/extraction/names.py` — экстракция ФИО (эвристика)
- `src/court_monitor/extraction/filtering.py` — фильтр релевантности
- `src/court_monitor/matching/score.py` — scoring с весами, BirthDateEvidence
- `src/court_monitor/matching/candidates.py` — generation match candidates
- `src/court_monitor/matching/name_normalizer.py` — морфологическая нормализация ru-name-v2
- `src/court_monitor/sources/fedsfm.py` — парсер XML/DBF/ZIP/CSV для Росфинмониторинга
- `src/court_monitor/storage/orm.py` — ORM-модели (SourceDocument, ExtractedFact, PersonRecord, MatchCandidate)
- `src/court_monitor/domain/models.py` — доменные enum'ы
- `src/court_monitor/api/app.py` — FastAPI read-only surface
- `config/sources.yaml` — адаптеры источников (sudrf)
- `config/monitoring.yaml` — статьи УК и ключевые слова
- `config/source_registry.yaml` — реестр Telegram-каналов

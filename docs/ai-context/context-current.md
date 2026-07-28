# Контекст — 2026-07-28 (обновлено)

## Что сделано
- Etap 0: Discovery — исследование источников (sudrf.ru, Росфинмониторинг, Airtable, Telegram), fixtures-first архитектура, модель данных.
- Etap 1: Каркас пакета + вертикальный срез — конфиг, домен, хранилище, источники, парсеры, экстракция, сервисы, CLI, API, логирование. Миграции Alembic.
- Etap 2: Интеграция парсера sudrf_press в pipeline, реестр источников из Airtable, probe/checkout команды.
- Etap 3: Структурированная экстракция — статьи УК, даты, ФИО. Фильтр релевантности по monitoring.yaml.
- Росфинмониторинг — импорт из XML/DBF/ZIP/CSV, нормализация ФИО, дедупликация по (source, normalized_name, birth_date).
- Explainable matching — морфологическая нормализация (ru-name-v2), scoring с весами,BirthDateEvidence для precision, generation candidates.
- CLI: init-db, migrate, doctor, show-config, fetch-source (sudrf + fedsfm), fetch-all, parse-pending, reprocess-document, list-documents, show-document, show-stats, list-sources, fetch-demo-source, import-source-registry, check-sources, list-person-records, show-person-record, generate-matches, list-matches, show-match, confirm-match, reject-match, list-review-items, resolve-review-item.
- FastAPI: /health, /stats, /documents, /documents/{id}.
- Telegram-источники: `process_registry_source` + `TelegramChannelAdapter` полностью подключены к pipeline (fetch fixture/`--live` → ingest → parse → extract). Исправлен баг — `parse_and_extract` теперь для source_type != sudrf использует уже очищенный `doc.text` вместо повторного прогона через sudrf-специфичный HTML-парсер (который подмешивал UI-мусор Telegram в текст для экстракции).
- Расширено тестовое покрытие `matching/` (birthplace scoring, BirthDateEvidence парсинг, mixed full-name/initial matching, invariant-тест на surname mismatch) и `fedsfm` (DBF/ZIP edge cases, форматы дат, dedup_key, CSV без ФИО).
- 167 тестов проходят (ruff + mypy чистые).
- Мелкий техдолг закрыт: убрано дублирование в CLI `doctor`, `print("already_exists")` заменён на structured log.
- `.venv` пересоздан (был битый симлинк на python другого пользователя/хоста).
- **Etap 4, срез 1** — таблицы `ReviewItem` (generic очередь проверки оператора) и `AuditLog` (append-only аудит решений), миграции `0006_review_items`/`0007_audit_log`. `ReviewItem` создаётся в `parse_and_extract` при `parser_status=parser_failed`. `AuditLog` пишется атомарно внутри `repo.update_match_status` (confirm/reject-match) и `repo.resolve_review_item`, с `actor` (CLI `--operator`, default `getpass.getuser()`) и `correlation_id`. CLI: `list-review-items`, `resolve-review-item [--dismiss]`. Person/PersonAlias/Case/PersonCase/CourtEvent сознательно не введены — см. `technical-debt.md` D-001. Смёржено в master (PR #1).
- **D-011 закрыт** — `sources.base.FetchProblem` (новый тип рядом с `FetchResult`); `SudrfAdapter`/`TelegramChannelAdapter.fetch_new()` возвращают `Iterator[FetchResult | FetchProblem]` вместо молчаливого `continue`/`return None` при `FetchHealth.blocked`/`http_error`/`timeout`/пустом теле. `process_source`/`process_registry_source` на `FetchProblem` создают `ReviewItem(item_type="source_blocked")`. `repo.upsert_review_item` расширен: дедуп по `source_id`, если нет `document_id`. Новое поле `SourceStats.blocked` (видно в `fetch-source`/`fetch-all`). `not_modified` и fixture-missing НЕ создают ReviewItem (это не сбои источника).
- 208 тестов проходят (ruff + mypy чистые).

## Следующий шаг
- Airtable write-back: запись подтверждённых кандидатов в Airtable (после валидации маппинга) — заблокировано отсутствием PAT.
- Etap 4, срез 2 (кандидат): `Person`/`Case`/`CourtEvent` — нужен либо экстрактор структурированных case/event-фактов (сейчас есть только article/date/name/relevance), либо политика авто-создания Person из подтверждённого MatchCandidate. `EventType` enum уже существует в `domain/models.py`, но не используется.
- `list-audit-log` CLI — сейчас аудит смотрится только через sqlite напрямую.
- `_extract_place_from_fact` в `matching/candidates.py` всегда возвращает `None` — birthplace-скоринг протестирован, но не используется в реальных кандидатах, пока нет источника места рождения из документа.
- `known-risks-and-notes.md`: `birth_date_conflict` в scoring почти недостижим (year-match branch перехватывает раньше) — задокументировано, не исправлялось (сознательно, scoring — чувствительная зона).

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
- `src/court_monitor/sources/base.py` — FetchResult/FetchProblem, SourceAdapter Protocol
- `src/court_monitor/storage/orm.py` — ORM-модели (SourceDocument, ExtractedFact, PersonRecord, MatchCandidate, ReviewItem, AuditLog)
- `src/court_monitor/domain/models.py` — доменные enum'ы
- `src/court_monitor/api/app.py` — FastAPI read-only surface
- `config/sources.yaml` — адаптеры источников (sudrf)
- `config/monitoring.yaml` — статьи УК и ключевые слова
- `config/source_registry.yaml` — реестр Telegram-каналов
